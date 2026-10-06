# -*- coding: utf-8 -*-
"""
kb_qa.py — 在线答疑增强层（吸收 WeKnora v0.8.2 的检索侧精华）

myme 原来答疑的链路是「一个问题 → hybrid_query 取 top_k → compress_context → 生成」。
这条路对**直问直答**够用，遇到下面两种情况会明显掉分：
  ① 问题里含多个意图（“ coordinate 怎么导，导完怎么备份？”）—— 单查询只命中前半句；
  ② 检索回来的片段彼此高度重复（PPT 每页都带相同的页眉/术语），白占上下文窗口。

WeKnora 的处理是把它交给 ReAct Agent 去分步规划；但对答疑这种轻量场景不该上 Agent
（慢、贵、还容易跑飞）。所以这里只取它检索侧的三种做法，成本压到最小：

  1. **多查询扩展（query expansion）**：先把问题拆成 2~3 个子问法，各自检索后合并；
     只有拆分失败（模型没给结果）才退回单查询 —— 永远保证能答。
  2. **候选去重 + 降权（filter_top_k 思路）**：按「正文指纹 + 相似向量」去重，
     重复片段只留分最高的一条，避免上下文里三句话其实是同一段话。
  3. **上下文再压缩**：复用 knowledge_base.compress_context，把候选压到 token 预算内。

追问（history）直接把上一轮问答塞进系统提示，不另开一轮检索 —— 缓存式多轮，开销可控。

用法：
    python kb_qa.py "维护周期是多久？"
    python kb_qa.py "怎么导入数据？" --names myme_kb --history 上轮Q|上轮A
"""
import hashlib
import json
import os
import re
import time

import config

MAX_EXPAND = 3
MAX_CANDIDATES = 12          # 扩展检索后最多留这么多候选再去重/压缩


# ============================================================
# ① 查询扩展
# ============================================================
EXPAND_PROMPT = """把用户的问题拆成 {n} 个较短的子问题，分别用不同说法去检索资料。
要求：
- 只输出 JSON 数组，形如 ["子问题1", "子问题2"]，不要解释、不要 ``` 包裹；
- 子问题必须覆盖原问题的各个意图；原问题只有一个意图时，直接输出只含 1 个元素的数组；
- 沿用原问题的专业术语（用户/资料里就是这么叫的）。

用户问题：{q}

JSON："""


def expand_queries(question, n=MAX_EXPAND):
    """把问题拆子问法。任何异常都退回 [question] —— 宁可少拆，不能问不出去。"""
    try:
        import llm_client
        raw = llm_client.chat([{"role": "user",
                               "content": EXPAND_PROMPT.replace("{n}", str(n))
                                                       .replace("{q}", question)}],
                              max_tokens=400, temperature=0.2)
        raw = (raw or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        arr = []
        try:
            arr = json.loads(raw)
        except Exception:
            m = re.search(r"\[[^\]]*\]", raw, re.S)
            if m:
                try:
                    arr = json.loads(m.group(0))
                except Exception:
                    arr = []
        out, seen = [], set()
        for x in arr:
            if isinstance(x, dict):
                x = x.get("q") or x.get("question") or x.get("sub") or ""
            x = str(x or "").strip()
            if x and x not in seen and x != question:
                seen.add(x)
                out.append(x)
        return (out or [question])[:n]
    except Exception:
        return [question]


# ============================================================
# ② 检索 + 去重
# ============================================================
def _fp(text):
    return hashlib.md5((text or "")[:400].encode("utf-8", "ignore")).hexdigest()


def _too_similar(a, b, thresh=0.92):
    """轻量余弦（候选数少，O(n²) 完全可接受），高于阈值判重复。"""
    try:
        import math
        x = a if isinstance(a, list) else b
        y = b if isinstance(a, list) else a
        if not x or not y:
            return False
        dot = sum(p * q for p, q in zip(x, y))
        na = math.sqrt(sum(p * p for p in x))
        nb = math.sqrt(sum(q * q for q in y))
        return na and nb and (dot / (na * nb)) > thresh
    except Exception:
        return False


def retrieve_multi(question, names=None, top_k=6, threshold=0.15, expand=True):
    """多查询扩展检索 → 合并 → 指纹去重 + 向量去重 → 返回候选列表。"""
    try:
        import knowledge_base
    except Exception as e:
        print("[qa] 无法导入 knowledge_base：%s" % e)
        return []

    names = names or ["myme_kb"]
    queries = expand_queries(question) if expand else [question]
    seen_fp, cands = set(), []
    for q in queries:
        try:
            hits = knowledge_base.hybrid_query(q, name=names[0], top_k=top_k,
                                               threshold=threshold)
        except Exception:
            hits = []
        for h in hits:
            fp = _fp(h.get("text"))
            if fp in seen_fp:                       # 同一片段（含同一页）只留一条
                continue
            seen_fp.add(fp)
            cands.append(h)
    # 向量层面再去重：不同页但内容为同一段话（常见：PPT 备注与正文重复）
    uniq = []
    for c in cands:
        dup = False
        for u in uniq:
            ea, eb = u.get("vec"), c.get("vec")
            if ea is not None and eb is not None and _too_similar(ea, eb):
                dup = True
                break
        if not dup:
            uniq.append(c)
    uniq.sort(key=lambda x: -(x.get("score") or 0))
    return uniq[:MAX_CANDIDATES]


# ============================================================
# ③ 组装上下文 + 追问
# ============================================================
def build_context(cands, max_chars=None):
    """把候选拼成带出处的上下文。每条都带「来源·章节」，生成后能标出处。"""
    max_chars = max_chars or int(os.environ.get("MYME_QA_CTX_CHARS", "6000"))
    parts, used, total = [], [], 0
    for c in cands:
        loc = c.get("label") or c.get("source") or "知识库"
        bc = c.get("breadcrumb")
        head = "【%s%s】" % (bc + " · " if bc else "", loc)
        body = (c.get("text") or "").strip()
        if not body:
            continue
        block = "%s\n%s" % (head, body)
        if total + len(block) > max_chars:
            break
        parts.append(block)
        used.append(c)
        total += len(block)
    return "\n\n".join(parts), used


def normalize_history(history):
    """把历史问答统一成 [(问, 答), …]，三种写法都收：

      - None / ""（无历史）
      - [(问,答), …] 已经是元组列表（脚本 & 内部调用）
      - "上一问|上一答" 或 "问1|答1 && 问2|答2" —— UI 一个文本框不好塞列表，
        就约定用 && 分隔轮次、每轮里第一个 | 分隔问与答（中英文竖线都认）。
      ⚠ 不接受裸字符串：以前直接把字符串丢给 answer_with_history，
        `for q, a in hist[-3:]` 会把字符串当字符序列拆，报 'str' object has no attribute 'get'。
    """
    if not history:
        return []
    if isinstance(history, dict):
        return [(str(history.get("q") or history.get("question") or ""),
                 str(history.get("a") or history.get("answer") or ""))]
    if isinstance(history, str):
        out = []
        for rnd in re.split(r"&&|\n", history):
            rnd = rnd.strip()
            if not rnd:
                continue
            if "|" in rnd or "｜" in rnd:
                q, a = re.split(r"[|｜]", rnd, maxsplit=1)
            else:
                q, a = rnd, ""
            if q.strip():
                out.append((q.strip(), a.strip()))
        return out[-3:]
    out = []
    for h in history:
        if isinstance(h, (list, tuple)) and len(h) >= 2:
            out.append((str(h[0] or ""), str(h[1] or "")))
        elif isinstance(h, dict):
            out.append((str(h.get("q") or h.get("question") or ""),
                        str(h.get("a") or h.get("answer") or "")))
    return out[-3:]


def _history_block(pairs):
    if not pairs:
        return ""
    lines = ["【上一轮问答（用户接着往下问，下面是上下文，不要重复回答）】"]
    for q, a in pairs:
        lines.append("问：%s\n答：%s" % (q, (a or "")[:400]))
    return "\n".join(lines)


def answer(question, names=None, history=None, top_k=6, threshold=0.15,
           expand=True, max_chars=None):
    """完整答疑：扩展检索 → 去重 → 压缩 → 生成。返回 {answer, sources, ctx_chars, secs}。

    history 支持上面 normalize_history 的三种写法；追问时**仍然会检索当前问题**
    （并保留出处），只是把上一轮问答压进上下文 —— 早先的写法追问直接跳过检索、
    返回空 sources，用户追问完看不到依据，等于把「带出处」这个优势弄丢了。"""
    t0 = time.time()
    hist = normalize_history(history)
    cands = retrieve_multi(question, names=names, top_k=top_k,
                           threshold=threshold, expand=expand)
    if not cands:
        import knowledge_base
        cands = knowledge_base.hybrid_query(question, name=(names or ["myme_kb"])[0],
                                            top_k=top_k, threshold=0.0)

    try:
        import knowledge_base
        ctx, used = build_context(cands, max_chars=max_chars)
        if used:
            # ⚠ compress_context 返回的是 **(压缩文本, 统计字典)**，不是「保留的候选列表」。
            #   早先写成 `ctx, used = ...`，第二个值就变成统计 dict，下面 sources 循环
            #   是在遍历它的键（字符串），报 'str' object has no attribute 'get'。
            #   （CLI 里 max_chars=None 会走 except 兜底，恰好把这个 bug 遮住了，
            #    只有从 UI 传 max_chars 才炸 —— 典型的「自己跑没事、用户一用就报错」。）
            # 上游 max_chars 是「字符数」，compress_context 收的是 token，按中文 1 token≈1.6 字折算
            ctx, _stats = knowledge_base.compress_context(
                question, used, max_tokens=max(500, int(max_chars / 1.6)))
    except Exception:
        ctx, used = build_context(cands, max_chars=max_chars)

    if hist:
        ctx = (_history_block(hist) + "\n\n" + ctx).strip()
    from qa_brain import answer_question
    ans = answer_question(question, ctx)

    sources = []
    for c in (used or []):
        sources.append({"label": c.get("label") or c.get("source") or "知识库",
                        "page": c.get("page"),
                        "breadcrumb": c.get("breadcrumb") or "",
                        "score": c.get("score"),
                        "text": (c.get("text") or "")[:180]})
    return {"answer": ans, "sources": sources, "candidates": len(cands),
            "ctx_chars": len(ctx), "secs": round(time.time() - t0, 1)}


def answer_with_history(question, history=None, **kw):
    """追问入口：只做参数归一（字符串/元组都行），真正的检索+生成交给 answer。

    不另起一套只拼提示、不检索的链路 —— 那样追问时 sources 会空，
    「每条结论标出处」就只对新问题成立，接着问一句出处全消失。"""
    return answer(question, history=normalize_history(history), **kw)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="答疑增强（多查询扩展 + 去重 + 来源标注）")
    ap.add_argument("question")
    ap.add_argument("--names", default="myme_kb")
    ap.add_argument("--history", default="", help="上一轮问答，格式：上轮问题|上轮答案")
    ap.add_argument("--no-expand", action="store_true", help="关掉查询扩展（对照用）")
    a = ap.parse_args()

    names = [x.strip() for x in a.names.split(",") if x.strip()]
    t0 = time.time()
    # history 直接给原字符串即可（normalize_history 认 "Q|A" / "Q1|A1 && Q2|A2"）
    r = answer(a.question, names=names, expand=not a.no_expand,
               history=a.history or None)
    print("检索候选 %d 条 / 上下文 %d 字 / 共用 %.1fs"
          % (r.get("candidates", 0), r.get("ctx_chars", 0), time.time() - t0))
    print("---- 答案 ----")
    print(r.get("answer", ""))
    if r.get("sources"):
        print("---- 来源 ----")
        for s in r["sources"]:
            print("  · %s%s（score %s）"
                  % (s["breadcrumb"] + " · " if s.get("breadcrumb") else "",
                     s["label"], s.get("score")))
