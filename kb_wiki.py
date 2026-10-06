# -*- coding: utf-8 -*-
"""
kb_wiki.py — AI 自动 Wiki（吸收 WeKnora v0.8.2 的第三层「AI 自动 Wiki」）

WeKnora 那层最值钱的地方不是「让模型写文章」，而是三件事组合起来：
  ① 结构化：Wiki 不是一坨长文，而是**相互链接**的页面 + 一份索引；
  ② 可追溯：每个页面里的话都有出处，能回到原始文档那一页；
  ③ 能演进：原始文档更新后，Wiki 是「重新生成」而不是手工维护，支持万级文档体量。

这里的落地版（纯本地、不引 Go 侧任何东西）：
  1) Planner：先让模型把语料拆成主题清单（JSON），避免一锅炖；
  2) Writer ：每个主题回自己的知识库做一次混合检索（语义+BM25），把检索结果当
              原料写页面 —— 这一步保证「不是编的」，也让来源标注有得可依；
  3) 链接化：页面间用 [[主题名]] 互链，生成后扫描链接成本地图谱（页面节点 +
              [[引用]] 边），既能画 mermaid，也能给前端做知识图谱；
  4) 可复算：随时 rebuild，原始语料变了 Wiki 就跟着变。

用法：
    python kb_wiki.py                       # 用默认知识库生成到 DATA_DIR/wiki
    python kb_wiki.py --names myme_kb public --out D:/wiki
    python kb_wiki.py --topics 6 --dry      # 只出主题规划，不写页面（看规划合不合理）
"""
import json
import os
import re
import time

import config

# ============================================================
# 提示词（刻意写得「约束强」：模型输出必须能被程序解析，否则页面质量不可控）
# ============================================================
PLAN_PROMPT = """你是一个中文知识库的结构编辑。下面是一批原始资料的内容摘要。

请据此规划 Wiki 的页面结构。要求：
- 输出 **严格 JSON**，数组，不要任何解释文字、不要 ``` 代码块包裹；
- 每个元素形如 {{"topic":"主题名","question":"用户会怎么问这个问题","keywords":["关键词"]}}；
- 主题名 ≤ 12 字，彼此不重叠，覆盖资料里的核心议题（不要拆太细，5~{n} 个为宜）；
- 若资料里反复出现某个专有名词/报错码/流程步骤，必须单独成条。

资料摘要：
{docs}

JSON："""

WRITE_PROMPT = """你是这个知识库的 Wiki 撰稿人。用下面【检索到的原文】回答【主题】，
写成一篇中文 Markdown 页面。

硬性要求：
1. 只依据【检索到的原文】，不要凭通用知识编造；原文没覆盖的就写「（原始资料未涵盖）」；
2. G 结尾可用数字/步骤，尽量把流程写成可执行的步骤；
3. 在关键句后面用 [来源: 文件名#页码或章节] 标出处，宁可少标也要标对；
4. 需要提到别的页面时，用 [[另一个主题名]] 这种双括号互链，且链接名字**必须**从下面候选里原样挑一个
   （候选：{titles}）；候选之外的内容（人名、部门名、报错码）不要用双括号包，直接写文字；
5. 开头给一段 2~3 句摘要，末尾给「## 相关页面」列出 [[链接]]。

【主题】{topic}
【用户可能会问】{question}
【检索到的原文】
{context}

请只输出 Markdown 正文。"""


# ============================================================
# 基础工具
# ============================================================
def _fmt_secs(t):
    return "%.1fs" % t if t < 60 else "%.0fs" % t


def _strip_code(s):
    """把模型回答外面的 ``` 包裹剥掉（很多模型爱带 markdown 围栏）。"""
    s = (s or "").strip()
    s = re.sub(r"^```(?:markdown|md|json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _llm_chat(messages, max_tokens=None, temperature=0.3):
    """调 LLM 并做容错：模型返回空/截断时不抛，交给上层降级。"""
    try:
        import llm_client
        return llm_client.chat(messages, max_tokens=max_tokens, temperature=temperature)
    except Exception as e:
        print("[wiki] 模型调用失败：%s" % e)
        return ""


# ============================================================
# ① 拉语料
# ============================================================
def gather_corpus(names=None, per_doc=2500, max_docs=40):
    """从指定知识库里取回原文摘要。返回 [(source, text)]。

    ⚠ 这里读的是 store 里每块的原文（不是嵌入），所以取每块前 N 字符即可；
      块本身已经按上下文切好，直接拼不会丢结构。
    """
    try:
        import knowledge_base
    except Exception as e:
        print("[wiki] 无法导入 knowledge_base：%s" % e)
        return []

    names = names or ["myme_kb"]
    out, seen = [], set()
    for n in names:
        try:
            store = knowledge_base.load_kb(n)
        except Exception as e:
            print("[wiki] 读知识库 %s 失败：%s" % (n, e))
            continue
        chunks = store.get("chunks", [])
        if not chunks:
            print("[wiki] 知识库 %s 是空的，跳过" % n)
            continue
        buf, src = [], ""
        for c in chunks:
            s = (c.get("meta") or {}).get("source") or (c.get("meta") or {}).get("name") or n
            src = s
            if s not in seen:
                seen.add(s)
            buf.append(c.get("text", "")[:per_doc])
            if sum(len(x) for x in buf) > 40000:
                break
        if buf:
            out.append((src, "\n\n".join(buf)[: max(per_doc * max_docs, 40000)]))
    return out


def corpus_blob(names=None, per_doc=2500, max_docs=40):
    """把语料拼成给模型看的整段文本（每份原文前加 `## 来源名` 当小标题）。

    UI 上「先出选题清单」要先拿语料再规划，不重跑模型就得复用这一份，
    所以抽成函数 —— 否则两处各拼一次，语料口径不一致（顺序、截断）就对不上了。"""
    corpus = gather_corpus(names=names, per_doc=per_doc, max_docs=max_docs)
    return "\n\n".join("## %s\n%s" % (s, t) for s, t in corpus)


def _parse_topics(raw):
    """从模型回答里抠出主题数组。小模型（9B）常带前后 Explanation、``` 围栏、
    或在数组前后塞一句「以下是规划结果」，纯 json.loads 会直接失败——
    所以三级兜底：整段解析 → 抠第一个 [...] 块 → 逐行抠 {...}。"""
    topics = []

    def push(it):
        if isinstance(it, dict):
            t = str(it.get("topic") or it.get("title") or "").strip()
            if not t:
                return
            topics.append({"topic": t[:24],
                           "question": str(it.get("question") or it.get("desc")
                                           or it.get("summary") or t).strip()[:80],
                           "keywords": it.get("keywords") or []})

    raw = _strip_code(raw or "")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("topics") or data.get("pages") or []
        if isinstance(data, list):
            for it in data:
                push(it)
        return topics
    except Exception:
        pass

    m = re.search(r"(\[\s*\{.*?\}\s*\])", raw, re.S)
    if m:
        try:
            for it in json.loads(m.group(1)):
                push(it)
            if topics:
                return topics
        except Exception:
            pass

    for m in re.finditer(r"\{[^{}]*\"topic\"[^{}]*\}", raw):
        try:
            push(json.loads(m.group(0)))
        except Exception:
            continue
    return topics


def plan_topics(docs_blob, n=8):
    """让模型规划页面结构；解析不出结构化结果时退化为「按语料源拆主题」。"""
    prompt = PLAN_PROMPT.replace("{docs}", docs_blob[:30000]).replace("{n}", str(n))
    raw = _strip_code(_llm_chat([{"role": "user", "content": prompt}],
                                max_tokens=1600, temperature=0.4))
    topics = _parse_topics(raw)
    if not topics:
        print("[wiki] 主题规划没能解析出结构化结果，改用按段落切分的兜底主题")
        topics = _fallback_topics(docs_blob, n)
    # 去重（小模型常给出两个一模一样的主题）
    seen, uniq = set(), []
    for t in topics:
        k = t["topic"]
        if k in seen or not k:
            continue
        seen.add(k)
        uniq.append(t)
    return uniq[:n]


def _fallback_topics(docs_blob, n=8):
    """模型没给结构化规划时的兜底主题。

    ⚠ 不能退化成一路「概览」：早期版本只抓 Markdown 小标题，而语料是 PPT 排出的
      无标题正文，结果 n 个主题全是「概览」，Wiki 直接不可用。这里改成
      「按段落首句」+「按来源文件名」两条路子各取一批，保证主题彼此可区分。
    """
    heads, seen = [], set()

    def add(t):
        t = re.sub(r"^#+\s*", "", (t or "")).strip("：: 　")
        t = re.sub(r"^【?(来源|来源文件)\s*[:：]?\s*", "", t)
        if t and len(t) <= 24 and t not in seen:
            seen.add(t)
            heads.append(t)
            return True
        return False

    for l in docs_blob.split("\n"):
        ls = l.strip()
        if not ls:
            continue
        if re.match(r"^#{1,4}\s+\S", ls) or re.match(r"^第[一二三四五六七八九十]+[章节]", ls):
            add(re.sub(r"^#+\s*", "", ls))
        if len(heads) >= n:
            break
    for seg in docs_blob.split("\n\n"):
        if len(heads) >= n:
            break
        s = seg.strip().split("\n")[0].strip()
        s = re.sub(r"^【[^】]*】", "", s)[:20]          # 去掉【PPT第N页】这类页眉
        if len(s) >= 4:
            add(s)
    while len(heads) < min(3, n):
        add("知识库主题%d" % (len(heads) + 1))
    return [{"topic": h, "question": h, "keywords": []} for h in heads[:n]]


# ============================================================
# ② 检索原料 + ③ 写页面
# ============================================================
def retrieve(topic, question, names=None, top_k=6, max_chars=None):
    """围绕主题做一次混合检索，给写页面当原料（保证有出处、不是编的）。

    max_chars 同 kb_qa：给上下文预算（装配后超出即截断），避免整页塞爆模型上下文。"""
    try:
        import knowledge_base
    except Exception:
        return []
    q = question or topic
    try:
        res = knowledge_base.hybrid_query(q, name=(names or ["myme_kb"])[0], top_k=top_k)
    except Exception as e:
        print("[wiki] 检索失败：%s" % e)
        return []
    if max_chars:
        used = 0
        keep = []
        for r in (res or []):
            n = len((r.get("text") or ""))
            if used + n > max_chars:
                continue
            used += n
            keep.append(r)
        res = keep or (res or [])[:1]
    return res


def write_page(topic, question, results, snippet=2500, page_titles=""):
    """写页面。page_titles 是把其它页面的 topic 列给模型看 —— 不给候选，
    模型会写出 [[科技推广中心]] 这种实体名当链接，图谱里一条边都连不上。"""
    if not results:
        return ("# %s\n\n（原始资料里没有检索到与「%s」直接相关的内容，"
                "本页待资料补齐后重建。）\n" % (topic, topic))
    ctx_parts = []
    for i, r in enumerate(results):
        label = r.get("label") or r.get("source") or "来源%d" % (i + 1)
        bc = r.get("breadcrumb")
        head = "%s%s" % ((" %s " % bc) if bc else "", label)
        ctx_parts.append("【片段%d · %s】\n%s" % (i + 1, head, (r.get("text") or "")[:snippet]))
    ctx = "\n\n".join(ctx_parts)
    prompt = WRITE_PROMPT.replace("{topic}", topic).replace("{question}", question)
    prompt = prompt.replace("{context}", ctx)
    if page_titles:
        prompt = prompt.replace("{titles}", page_titles)
    else:
        prompt = prompt.replace("{titles}", "（本次只有这一页，暂不互链）")
    body = _strip_code(_llm_chat([{"role": "user", "content": prompt}],
                                 max_tokens=2400, temperature=0.35))
    return body or ("# %s\n\n（模型没有产出内容）\n" % topic)


# ============================================================
# ④ 图谱 + 索引 + 落盘
# ============================================================
RE_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
# 页面里标出处时写的是 [来源: PPT 第 4 页#第二部分]，图谱要靠它找共被引
RE_SOURCE = re.compile(r"\[来源[:：]\s*([^\]\[#]+)")


def scan_links(body):
    return [l.strip() for l in RE_WIKILINK.findall(body)]


def _body_sources(body):
    """从正文里抠 [来源: …] —— 页面落盘后（无 sources 字段）也能算共被引。"""
    return [s.strip() for s in RE_SOURCE.findall(body or "")]


def build_graph(pages):
    """页面 → 节点 + [[互链]] 边；顺带统计每页的出/入度（前端画图谱用）。

    边有两条来源：
      ① 显式互链：正文里的 [[页面标题]] 命中了真实存在的页面（模型写的）；
      ② 共被引兜底：两页检索/引用了同一个出处（如同一页 PPT）就连一条虚边 ——
         模型偶尔会把 [[京引信息化工作群]] 这种实体名当链接写，纯靠 ① 会得到
         「6 个节点 0 条边」的空图谱，看着像功能坏了。共被引是确定性的、总能连上。
    """
    names = {p["topic"] for p in pages}
    nodes = [{"id": p["topic"], "title": p["topic"], "file": p["file"],
              "chars": len(p["body"])} for p in pages]
    edges = []

    def _add(a, b, kind):
        """每对页面只留一条边：显式互链优先，共被引作为补充依据记在 via 里。"""
        if a == b:
            return
        for e in edges:
            if {e["from"], e["to"]} == {a, b}:
                if kind.startswith("co-cite"):
                    if kind not in e.get("via", []):
                        e.setdefault("via", []).append(kind)
                return
        edges.append({"from": a, "to": b, "kind": kind, "via": [kind]})

    for p in pages:
        for tgt in p["links"]:
            if tgt in names and tgt != p["topic"]:
                _add(p["topic"], tgt, "link")
    # 共被引：同出处（来源名）出现在两页 → 连边
    src_map = {}
    for p in pages:
        for s in (p.get("sources") or _body_sources(p.get("body") or "")):
            key = str(s).strip()
            if key:
                src_map.setdefault(key, []).append(p["topic"])
    for key, owners in src_map.items():
        uniq = sorted(set(owners))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                _add(uniq[i], uniq[j], "co-cite:" + key)
    indeg = {p["topic"]: 0 for p in pages}
    for e in edges:
        indeg[e["to"]] += 1
    for n in nodes:
        n["in"] = indeg[n["id"]]
        n["links"] = sum(1 for e in edges if e.get("kind") == "link"
                         and e["from"] == n["id"])
    return {"nodes": nodes, "edges": edges}


def _mermaid(graph):
    lines = ["```mermaid", "graph LR"]
    for e in graph["edges"]:
        lines.append("  %s --> %s" % (e["from"], e["to"]))
    lines.append("```")
    return "\n".join(lines)


def build_index(pages, graph):
    idx = ["# AI 自动 Wiki 索引", "",
           "本目录由 `kb_wiki.py` 依据本地知识库自动生成：主题由模型规划，"
           "正文来自混合检索（语义 + BM25）返回的原文，链接可跳回出处。",
           "原始资料更新后重新执行 `python kb_wiki.py` 即可整包刷新。", ""]
    idx.append("## 页面\n")
    for p in pages:
        idx.append("- [[%s]] —— %s 字" % (p["topic"], len(p["body"])))
    idx.append("")
    idx.append("## 知识图谱\n")
    idx.append(_mermaid(graph))
    idx.append("")
    idx.append("（图谱数据也在 `wiki_graph.json`，前端可直接拿去做力导向图）")
    return "\n".join(idx) + "\n"


def generate_wiki(names=None, out_dir=None, n_topics=8, per_doc=2500, max_docs=40,
                  progress=None, ctx_max_chars=None):
    """完整流程：拉语料 → 规划主题 → 逐页检索+撰写 → 索引/图谱 → 落盘。
    返回统计 dict。

    progress(i, total, topic, body) —— 每写完一页回调（UI 用它刷进度；可为 None）。
    ctx_max_chars —— 每页喂给模型的上文预算；None 表示不限制（每片段 2500 字）。"""
    t0 = time.time()
    out_dir = out_dir or os.path.join(config.DATA_DIR, "wiki")
    os.makedirs(out_dir, exist_ok=True)

    corpus = gather_corpus(names=names, per_doc=per_doc, max_docs=max_docs)
    if not corpus:
        print("[wiki] 没有可用语料，直接退出（先往知识库里导点东西）")
        return {"ok": False, "reason": "no corpus"}
    docs_blob = corpus_blob(names=names, per_doc=per_doc, max_docs=max_docs)
    print("[wiki] 语料 %d 份 / %.1f KB" % (len(corpus), len(docs_blob) / 1024.0))

    topics = plan_topics(docs_blob, n=n_topics)
    print("[wiki] 规划出 %d 个主题：%s" % (len(topics), "、".join(t["topic"] for t in topics)))

    titles = "｜".join(dict.fromkeys(t["topic"] for t in topics))
    pages = []
    for i, tp in enumerate(topics, 1):
        label = tp["question"] or tp["topic"]
        results = retrieve(tp["topic"], label, names=names, max_chars=ctx_max_chars)
        body = write_page(tp["topic"], label, results, page_titles=titles)
        fn = "%02d-%s.md" % (i, re.sub(r"[\\/:*?\"<>|]", "", tp["topic"])[:20])
        path = os.path.join(out_dir, fn)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        # sources：本页实际引用到的出处（供 build_graph 做「共被引」兜底连边）
        srcs = []
        for r in (results or []):
            s = r.get("source") or r.get("label") or ""
            if s and s not in srcs:
                srcs.append(s)
        pages.append({"topic": tp["topic"], "file": fn, "body": body,
                      "links": scan_links(body), "sources": srcs, "hits": len(results)})
        print("  [%d/%d] %s → 检索%d条 / 正文%d字 / %.1fs"
              % (i, len(topics), tp["topic"], len(results), len(body), time.time() - t0))
        if progress:
            try:
                progress(i, len(topics), tp["topic"], body)
            except Exception:
                pass

    graph = build_graph(pages)
    with open(os.path.join(out_dir, "wiki_graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "00-索引.md"), "w", encoding="utf-8") as f:
        f.write(build_index(pages, graph))
    _ = _fmt_secs(time.time() - t0)
    print("[✓] Wiki 生成完：%d 页 / 图谱 %d 节点 %d 边，用时 %.1fs → %s"
          % (len(pages), len(graph["nodes"]), len(graph["edges"]), time.time() - t0, out_dir))
    return {"ok": True, "pages": len(pages), "graph": graph,
            "out": out_dir, "secs": round(time.time() - t0, 1)}


def rebuild_graph(out_dir=None):
    """不重跑模型，只从已有页面重建知识图谱 + 00-索引（秒级）。

    改了提示词想重新算互链、或手工补过页面时用；完整重生成一次要十几分钟，
    而图谱/索引是从页面正文推出来的，没必要每次都从头跑。"""
    out_dir = out_dir or os.path.join(config.DATA_DIR, "wiki")
    pages = []
    for fn in sorted(os.listdir(out_dir)):
        if not (fn.endswith(".md") and not fn.startswith("00-")):
            continue
        body = open(os.path.join(out_dir, fn), encoding="utf-8").read()
        topic = re.sub(r"^\d+-", "", fn)[:-3]
        pages.append({"topic": topic, "file": fn, "body": body,
                      "links": scan_links(body), "sources": _body_sources(body)})
    if not pages:
        print("目录里没有 wiki 页面：%s" % out_dir)
        return {"ok": False, "reason": "no pages"}
    graph = build_graph(pages)
    with open(os.path.join(out_dir, "wiki_graph.json"), "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "00-索引.md"), "w", encoding="utf-8") as f:
        f.write(build_index(pages, graph))
    print("[✓] 图谱重建：%d 节点 / %d 边 → %s"
          % (len(graph["nodes"]), len(graph["edges"]), out_dir))
    return {"ok": True, "pages": len(pages), "graph": graph, "out": out_dir, "secs": 0.1}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="从本地知识库生成 AI Wiki")
    ap.add_argument("--names", default="myme_kb",
                    help="知识库名，逗号分隔（默认 myme_kb；多库用 public,myme_kb）")
    ap.add_argument("--out", default="", help="输出目录（默认 DATA_DIR/wiki）")
    ap.add_argument("--topics", type=int, default=8, help="最多几个主题")
    ap.add_argument("--dry", action="store_true", help="只出主题规划，不写页面")
    ap.add_argument("--rebuild", action="store_true",
                    help="不跑模型，只按已有页面重建图谱与 00-索引")
    a = ap.parse_args()
    if a.rebuild:
        raise SystemExit(0 if rebuild_graph(a.out or None).get("ok") else 1)

    names = [x.strip() for x in a.names.split(",") if x.strip()]
    t0 = time.time()
    corpus = gather_corpus(names=names)
    if not corpus:
        print("没拉到语料，先往知识库导文档。")
        raise SystemExit(2)
    blob = "\n\n".join("## %s\n%s" % (s, t) for s, t in corpus)
    topics = plan_topics(blob, n=a.topics)
    print("主题规划（%.1fs）：" % (time.time() - t0))
    for i, t in enumerate(topics, 1):
        print("  %d. %-24s  问法：%s" % (i, t["topic"], t["question"][:40]))
    if a.dry:
        raise SystemExit(0)
    r = generate_wiki(names=names, out_dir=(a.out or None), n_topics=a.topics)
    raise SystemExit(0 if r.get("ok") else 1)
