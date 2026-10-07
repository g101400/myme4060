# -*- coding: utf-8 -*-
"""
kb_graph.py — 知识图谱 + 图谱增强检索（吸收 WeKnora v0.8.2 的「知识图谱增强」）

WeKnora 的混合检索是「语义向量 + BM25 + 知识图谱」三路。前两路 myme 早先就都有了
（knowledge_base.hybrid_query），缺的是图谱这一路 —— 它专门兜住向量检索的老毛病：

  · 专有名词 / 缩写 / 报错码（"四预、一张图、PAF、SSI、F-01E"）—— 向量把它当普通词，
    容易和同音近义的词混在一起；
  · 概念链条（清淤 → 疏浚 → 工期 → 预算）—— 只命中链中间一环，答不了"它跟谁相连"。

做法（纯本地、零新依赖）：
  1. 抽实体：领域术语表（domain_water 的 terms）+ 形态规则（中文术语 / 英文缩写 /
     breadcrumb 层级节点）；
  2. 建图：同一 chunk 内共现的实体连边，权重 = 共现次数；
  3. 增强检索：命中 chunk 的实体 → 沿边扩散邻居 → 邻居 chunk 作为「补充候选」进上下文。

落盘：KP_ROOT/graph/<kb名>.json，可用 --rebuild 秒级重建。

用法：
    import kb_graph
    kb_graph.build("myme_kb")                       # 建/重建图谱
    cands = kb_graph.augment(query_hits, top_k=3)   # 给检索结果补邻居
    kb_graph.mermaid("myme_kb")                     # 出 Mermaid 图
CLI:
    python kb_graph.py myme_kb                      # 构建 + 统计
    python kb_graph.py myme_kb --term 一张图          # 看某个实体的邻居
"""

import os
import re
import json
import time
import math
import hashlib

import config

# ============================================================
# 路径
# ============================================================
def graph_dir():
    root = getattr(config, "KP_ROOT", None) or config.DIR_DATA
    d = os.path.join(root, "graph")
    os.makedirs(d, exist_ok=True)
    return d


def graph_path(kb="myme_kb"):
    return os.path.join(graph_dir(), "%s.json" % kb.split("/")[-1])


# ============================================================
# 实体抽取
# ============================================================
# 泛化术语形态：这类后缀的中文词几乎都是"概念节点"
RE_CN_TERM = re.compile(
    r"[一-龥]{2,8}(?:平台|系统|一张图|数字孪生|孪生|一张图|感知|网关|边缘|算法|模型|"
    r"库|闸|坝|渠|泵站|涵洞|堤防|水库|清淤|疏浚|灌区|水文|水质|水量|调度|预警|"
    r"稽核|监管|台账|档案|规范|标准|条例|办法|方案|工程|项目|设备|终端|传感|"
    r"采集|传输|平台|中枢|底座|中台)")

RE_ABBREV = re.compile(r"(?<![A-Za-z])([A-Z][A-Z0-9\-]{1,7})(?![A-Za-z])")
RE_EN_WORD = re.compile(r"(?<![A-Za-z])([a-z][a-z0-9]{2,15})(?![A-Za-z])")
RE_STOP_EN = {"the", "and", "for", "with", "that", "this", "from", "into", "your",
              "you", "are", "was", "not", "can", "will", "out", "all", "has", "have",
              "but", "its", "our", "one", "two", "use", "used", "using", "http",
              "html", "json", "html5", "word", "http1", "www", "com"}

# 领域术语由 domain_water 注入（水利古建 / 感知 / 三端四平台），取不到也不影响
def _domain_terms():
    out = []
    try:
        import domain_water
        for key in ("water", "heritage", "sensor"):
            cfg = domain_water.get(key) or {}
            t = cfg.get("terms") or []
            for x in t:
                # 术语表在 domain_water 里是 [[词, 释义], …]，从 v2.8 起也可能被改成
                # 单一词表，两种写法都认，否则宁可一条都不加（术语没对上比多一条更糟）
                w = x if isinstance(x, str) else (x[0] if isinstance(x, (list, tuple)) else "")
                if isinstance(w, str) and 2 <= len(w) <= 12:
                    out.append(w)
    except Exception:
        pass
    return out


# 这类开头几乎都是「短语被切开的一半」（如「以下两台设备」被截成「下两台设备」）
RE_PHRASE_FRAG = re.compile(
    r"^(?:以下|以上|上|下|前|后|内|外|中|各|每|本|该|此|多|几|两|三|四|五|六|七|八|九|十|"
    r"数|若干|所有|全部|其|之)[一二三四五六七八九十两数]*"
    r"(?:台|个|张|条|项|处|块|套|次|年|月|日|类|种|路|路|栋|座|道)?$")


def extract_entities(text, breadcrumb=""):
    """从一段文本里抽实体集合（含 breadcrumb 层级节点）。返回 list[str]。"""
    out = set()
    if breadcrumb:
        for part in re.split(r"[>·/|、]+", breadcrumb):
            part = part.strip()
            if len(part) >= 2 and len(part) <= 12:
                out.add(part)
    for m in RE_CN_TERM.findall(text or ""):
        if 2 <= len(m) <= 12 and not RE_PHRASE_FRAG.match(m):
            out.add(m)
    for m in RE_ABBREV.findall(text or ""):
        if len(m) >= 2 and m not in RE_STOP_EN:
            out.add(m)
    for m in _domain_terms():
        if m in (text or ""):
            out.add(m)
    return [x for x in out]


# ============================================================
# 构建
# ============================================================
def _fp(text):
    return hashlib.sha1((text or "")[:160].encode("utf-8", "ignore")).hexdigest()[:12]


def build(kb="myme_kb", max_chunks=4000, verbose=False, seed_terms=None):
    """扫知识库 chunks → 抽实体 → 共现连边。秒级完成，可反复重建。"""
    import knowledge_base
    store = knowledge_base.load_kb(kb)
    chunks = store.get("chunks") or []
    if max_chunks:
        chunks = chunks[:max_chunks]

    ent2fp = {}      # 实体 → [chunk指纹]
    fp2idx = {}      # 指纹 → chunk 下标
    nodes = {}       # 实体 → 权重（出现 chunk 数）
    edges = {}       # 实体 → {邻居: 共现次数}
    t0 = time.time()
    for i, c in enumerate(chunks):
        text = c.get("text") or ""
        f = _fp(text)
        fp2idx[f] = i
        ents = extract_entities(text, c.get("breadcrumb", ""))
        seen = []
        for e in ents:
            nodes[e] = nodes.get(e, 0) + 1
            ent2fp.setdefault(e, set()).add(f)
            seen.append(e)
        # 同一 chunk 内两两连边（权重+1），限制邻居条数防爆炸
        seen = sorted(set(seen), key=lambda x: -nodes.get(x, 0))[:40]
        for a in range(len(seen)):
            edges.setdefault(seen[a], {})
            for b in range(a + 1, len(seen)):
                edges[seen[a]][seen[b]] = edges[seen[a]].get(seen[b], 0) + 1

    # 过滤：太低频（噪声）与太高频（停用概念）都不适合当节点
    min_df, max_df = 2, max(20, len(chunks))
    keep = {k: v for k, v in nodes.items() if min_df <= v <= max_df}
    nodes = keep
    edges = {k: {n: w for n, w in v.items() if n in nodes and w >= 2}
             for k, v in edges.items() if k in nodes}
    # 领域术语种子：这些词哪怕只出现一次也必须是节点（水利/古建/感知的行话，
    # 只靠形态正则抽不全，而它们恰恰是最需要「顺着概念链」追问的那批）
    for s in (seed_terms or []):
        if isinstance(s, (list, tuple)):
            s = s[0] if s else ""
        s = str(s or "").strip()
        if 2 <= len(s) <= 12:
            nodes.setdefault(s, 1)
            edges.setdefault(s, {})

    data = {"kb": kb, "built": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chunks": len(chunks), "nodes": nodes, "edges": edges,
            "ent2fp": {k: sorted(v) for k, v in ent2fp.items()},
            "fp2idx": fp2idx}
    with open(graph_path(kb), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    stat = {"kb": kb, "nodes": len(nodes), "edges": sum(len(v) for v in edges.values()),
            "chunks": len(chunks), "secs": round(time.time() - t0, 2)}
    if verbose:
        print(json.dumps(stat, ensure_ascii=False))
    return stat


def load(kb="myme_kb"):
    p = graph_path(kb)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def rebuild(kb="myme_kb"):
    """重建：自动把三个领域档的术语灌成种子节点。"""
    return build(kb, seed_terms=_domain_terms())


# ============================================================
# 图谱增强检索
# ============================================================
def neighbors(term, kb="myme_kb", top_n=8):
    """某实体的邻居（按权重排序），用于界面展开看概念链。"""
    g = load(kb) or {}
    edges = g.get("edges", {}).get(term) or {}
    items = sorted(edges.items(), key=lambda x: -x[1])[:top_n]
    return [{"term": k, "weight": v} for k, v in items]


def augment(hits, kb="myme_kb", max_extra=3, max_hops=1):
    """给检索结果补「概念邻居」候选。

    hits 是 hybrid_query 的返回（list of dict，含 text/breadcrumb）。
    命中 chunk 里出现的高频实体 → 沿边取邻居实体 → 把邻居所在的 chunk 也拉进来，
    这样「清淤 → 工期」这类链条不会因为只命中一半而答偏。
    返回 (新候选列表, 追溯列表)。不改动原 hits。"""
    g = load(kb)
    if not g or not hits:
        return [], []
    nodes = g.get("nodes", {})
    ent2fp = g.get("ent2fp", {})
    fp2idx = g.get("fp2idx", {})

    import knowledge_base
    store = knowledge_base.load_kb(kb)
    chunks = store.get("chunks") or []
    by_fp = {}
    for i, c in enumerate(chunks):
        by_fp[_fp(c.get("text"))] = (i, c)

    have = {_fp(h.get("text")) for h in hits}
    # 实体优先级直接取图谱权重（出现次数越多，概念节点越实）
    scored = []
    for h in hits:
        for e in set(extract_entities(h.get("text"), h.get("breadcrumb", ""))):
            if e in nodes:
                scored.append((nodes[e], e))
    scored.sort(key=lambda x: -x[0])
    cand_fps, trace = {}, []
    trace_seen = set()
    for w, e in scored:
        if len(trace) >= max_extra:
            break
        nb = sorted((g.get("edges", {}).get(e) or {}).items(), key=lambda x: -x[1])
        if max_hops > 1:
            for n2, w2 in nb[:5]:
                for n3, w3 in sorted((g.get("edges", {}).get(n2) or {}).items(),
                                     key=lambda x: -x[1])[:5]:
                    if n3 != e:
                        nb.append((n3, w3 + w2))
        for n, w2 in nb[:max_extra]:
            if n in trace_seen:
                continue
            trace_seen.add(n)
            for f in ent2fp.get(n, []):
                if f in have or f in cand_fps:
                    continue
                cand_fps[f] = w2
                trace.append({"via": e, "term": n, "weight": w2})
    extra = []
    for f, w in sorted(cand_fps.items(), key=lambda x: -x[1]):
        hit = by_fp.get(f)
        if not hit:
            continue
        i, c = hit
        srcidx = fp2idx.get(f)
        extra.append({
            # 折算成与混合检索同量级的分数（共现 20 次≈满格），才能和原生候选一起排序、
            # 一起被 build_context 取舍；否则排在后半段，上下文一满就永远进不去
            "text": c.get("text"), "score": round(min(1.0, w * 0.05), 4), "vec": None,
            "page": (c.get("meta") or {}).get("page"),
            "title": (c.get("meta") or {}).get("title"),
            "source": (c.get("meta") or {}).get("source"),
            "label": (c.get("label") or (c.get("meta") or {}).get("source") or "知识库"),
            "breadcrumb": c.get("breadcrumb", ""), "kb": kb, "graph": True,
            "score_raw": round(w, 3),
        })
    return extra, trace


# ============================================================
# 可视化 / 统计
# ============================================================
def top_nodes(kb="myme_kb", n=20):
    g = load(kb) or {}
    items = sorted((g.get("nodes") or {}).items(), key=lambda x: -x[1])[:n]
    return [{"term": k, "weight": v} for k, v in items]


def stats(kb="myme_kb"):
    g = load(kb)
    if not g:
        return {"exists": False, "kb": kb}
    return {"exists": True, "kb": kb, "built": g.get("built"),
            "chunks": g.get("chunks"), "nodes": len(g.get("nodes") or {}),
            "edges": sum(len(v) for v in (g.get("edges") or {}).values())}


def mermaid(kb="myme_kb", top_n=18):
    g = load(kb) or {}
    nodes = g.get("nodes") or {}
    edges = g.get("edges") or {}
    top = sorted(nodes.items(), key=lambda x: -x[1])[:top_n]
    names = [k for k, _ in top]
    lines = ["graph TD"]
    for a in names:
        for b, w in sorted((edges.get(a) or {}).items(), key=lambda x: -x[1]):
            if b in names and b > a:
                lines.append('  "%s" --- |%d| "%s"' % (a, w, b))
    idx = {n: i for i, n in enumerate(names)}
    for a in names:
        lines.insert(1, '  "%s"[%d]' % (a, idx[a]))
    return "\n".join(lines)


def save_mermaid(kb="myme_kb", out=None):
    mk = mermaid(kb)
    out = out or os.path.join(config.KP_ROOT, "exports", "kb_graph_%s.md" % kb.split("/")[-1])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("```mermaid\n%s\n```\n" % mk)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="知识图谱增强（实体共现 + 邻居扩展检索）")
    ap.add_argument("kb", nargs="?", default="myme_kb")
    ap.add_argument("--term", default="", help="查看某实体的邻居")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--mermaid", action="store_true", help="导出 mermaid 到 exports")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    if a.term:
        print(json.dumps(neighbors(a.term, a.kb), ensure_ascii=False, indent=2))
    else:
        if a.rebuild or not load(a.kb):
            print(json.dumps(build(a.kb, verbose=True), ensure_ascii=False))
        print(json.dumps(stats(a.kb), ensure_ascii=False, indent=2))
        print("---- 高频节点 ----")
        for n in top_nodes(a.kb, a.top):
            print("  · %s（%d 处）" % (n["term"], n["weight"]))
        if a.mermaid:
            print("导出了：%s" % save_mermaid(a.kb))
