# -*- coding: utf-8 -*-
"""
myme.knowledge_base — 本地知识库（RAG，纯标准库）
用 llm_client 统一接入层做向量化（Ollama /api/embeddings 或 OpenAI-compatible /v1/embeddings），
知识块 + 向量持久化到 JSON；检索支持：
  - 向量检索（余弦相似度）
  - BM25 关键词检索（纯 Python，专业术语/编号精准命中）
  - 混合检索（向量 + BM25 加权融合）
  - 相似度阈值过滤（低于阈值直接丢弃，抑制幻觉）
  - 增量入库（按文件 sha256 比对，只重算新增/修改文档）
流程：文档 → 清洗 → 分块(带重叠) → 向量化入库 → 提问时混合检索 Top-K → 喂给大模型。
"""
import os
import re
import json
import math
import time
import zipfile
import urllib.request
import urllib.error
import config
from config import (EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP, RETRIEVE_TOP_K,
                    KB_COMPRESS, KB_COMPRESS_TOKENS, KB_COMPRESS_USE_LLM,
                    KB_NEAR_DUP_THR, KB_MIN_CHARS, KB_BACKUP_EXT)
import llm_client

# 项目私有库的库名（与 app.py 的 KB_NAME 保持一致）
KB_PROJECT_NAME = "myme_kb"


def _post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def ollama_embed(text, model=EMBED_MODEL, base=None):
    """文本向量化：统一走 llm_client（接本地 Ollama 或线上 OpenAI-compatible 服务）。

    模型接入由「设置 → 大模型接入」配置（base/key/model），无需硬编码。
    base 参数为兼容历史调用保留（已忽略），实际地址取自 llm_client 配置。
    """
    return llm_client.embed(text, model=model)


# ============================================================
# 分块（通用，带重叠）
# ============================================================
def _split_sentences(text):
    """按中英文句末标点切句，保留分隔符。"""
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text, meta=None, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """把一段文本切成带重叠的块，保留元数据（来源/页码/标题等）。"""
    sents = _split_sentences(text)
    chunks, cur, cur_len = [], [], 0
    for s in sents:
        if cur and cur_len + len(s) > size:
            chunks.append("".join(cur))
            # 重叠：保留末尾不超过 overlap 字符，保证上下文连贯
            tail = "".join(cur)
            if len(tail) > overlap:
                tail = tail[-overlap:]
            cur = [tail]
            cur_len = len(tail)
        cur.append(s)
        cur_len += len(s)
    if cur:
        chunks.append("".join(cur))
    return [{"text": c, "meta": dict(meta or {})} for c in chunks]


def chunk_slide(slide, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """把一页 PPT 内容切片（保留页码/标题元数据）。"""
    raw = f"【第{slide['idx']}页 {slide.get('title','')}】\n{slide.get('text','')}"
    if slide.get("notes"):
        raw += f"\n（备注）{slide['notes']}"
    meta = {"page": slide["idx"], "title": slide.get("title", ""),
            "source": f"PPT第{slide['idx']}页"}
    return chunk_text(raw, meta=meta, size=size, overlap=overlap)


# ============================================================
# 构建 / 持久化
# ============================================================
def kb_dir_for(name):
    """按知识库名解析其落盘目录，实现「公有 / 项目 / 分身」三层隔离。

    name 约定：
      "public"        → 公有通用库（DATA_DIR/kb_public）
      "person:<id>"   → 某个分身的私有库（DATA_DIR/kb_person/<id>）
      其他            → 当前项目私有库（config.DIR_KB）
    """
    n = name or ""
    if n == "public":
        return config.DIR_KB_PUBLIC
    if n.startswith("person:"):
        return os.path.join(config.DIR_KB_PERSON, n.split(":", 1)[1] or "_")
    return config.DIR_KB


def _store_path(name):
    return os.path.join(kb_dir_for(name), name + ".json")


def _save_store(store):
    os.makedirs(kb_dir_for(store.get("name")), exist_ok=True)
    path = _store_path(store["name"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    return path


def build_kb(slides, name="myme_kb", model=EMBED_MODEL):
    """分块 + 向量化 + 持久化到 JSON（PPT 全量重建）。返回统计。"""
    os.makedirs(config.DIR_KB, exist_ok=True)
    chunks = []
    for sl in slides:
        chunks.extend(chunk_slide(sl))
    for i, c in enumerate(chunks):
        c["embedding"] = ollama_embed(c["text"], model=model)
        c["src"] = f"ppt:{name}"
    store = {"name": name, "model": model, "chunks": chunks, "sources": {}}
    path = _save_store(store)
    return {"collection": name, "chunks": len(chunks), "path": path}


def load_kb(name="myme_kb"):
    with open(_store_path(name), encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 增量入库（文档：txt/md/docx/pptx）
# ============================================================
def ingest_document(path, name="myme_kb", category="", model=EMBED_MODEL):
    """把单个文档清洗→分块→向量化→增量并入知识库。
    - 按文件 sha256 去重：内容未变则跳过；内容变了则替换旧块。
    - 返回 {added, total, name, skipped}。"""
    import doc_ingest
    raw, meta = doc_ingest.extract(path)
    text = doc_ingest.clean_text(raw)
    if not text.strip():
        return {"added": 0, "total": 0, "name": meta.get("name"), "skipped": "empty"}
    h = doc_ingest.file_hash(path)
    title = doc_ingest.normalize_title(meta["name"], category)
    meta_doc = {"name": title, "source": title, "file": meta["name"],
                "type": meta["type"], "date": meta["date"],
                "status": "current"}
    chunks = chunk_text(text, meta=meta_doc)
    for c in chunks:
        c["embedding"] = ollama_embed(c["text"], model=model)
        c["src"] = h

    if os.path.exists(_store_path(name)):
        store = load_kb(name)
    else:
        store = {"name": name, "model": model, "chunks": [], "sources": {}}
    sources = store.setdefault("sources", {})
    # 增量优化：内容未变（同 hash 且块数一致）则跳过向量化，避免重复烧 GPU/CPU
    if h in sources and sources[h].get("n") == len(chunks):
        return {"added": 0, "removed": 0, "total": len(store["chunks"]),
                "name": title, "skipped": "unchanged"}
    # 移除同一来源的旧块（内容可能已修改）
    old = store["chunks"]
    store["chunks"] = [c for c in old if c.get("src") != h]
    removed = len(old) - len(store["chunks"])
    store["chunks"].extend(chunks)
    sources[h] = {"name": title, "file": meta["name"], "type": meta["type"],
                  "n": len(chunks)}
    _save_store(store)
    return {"added": len(chunks), "removed": removed,
            "total": len(store["chunks"]), "name": title}


def ingest_paths(paths, name="myme_kb", category="", model=EMBED_MODEL):
    """批量入库，逐文件返回结果。"""
    results = []
    for p in paths:
        try:
            results.append({"path": p, **ingest_document(p, name, category, model)})
        except Exception as e:
            results.append({"path": p, "error": str(e)})
    return results


def ingest_pptx(path, name="myme_kb", model=EMBED_MODEL):
    """把 PPT 增量并入知识库（保留每页页码元数据；按文件 hash 去重）。
    与 ingest_document 共用同一知识库，使"PPT 讲解"与"笔记/Word"问答共享上下文。"""
    import hashlib
    import pptx_parse
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    slides = pptx_parse.parse(path)
    chunks = []
    for sl in slides:
        chunks.extend(chunk_slide(sl))
    for c in chunks:
        c["embedding"] = ollama_embed(c["text"], model=model)
        c["src"] = h
    if os.path.exists(_store_path(name)):
        store = load_kb(name)
    else:
        store = {"name": name, "model": model, "chunks": [], "sources": {}}
    sources = store.setdefault("sources", {})
    old = store["chunks"]
    store["chunks"] = [c for c in old if c.get("src") != h]
    removed = len(old) - len(store["chunks"])
    store["chunks"].extend(chunks)
    sources[h] = {"name": os.path.basename(path), "file": os.path.basename(path),
                  "type": ".pptx", "n": len(chunks)}
    _save_store(store)
    return {"added": len(chunks), "removed": removed,
            "total": len(store["chunks"]), "name": os.path.basename(path)}


def list_sources(name="myme_kb"):
    if not os.path.exists(_store_path(name)):
        return []
    store = load_kb(name)
    return list(store.get("sources", {}).values())


# ============================================================
# 检索
# ============================================================
def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _tokenize(text):
    """混合中英文分词：英文/数字按词，中文按字（无 jieba 依赖）。"""
    text = (text or "").lower()
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)


def bm25_scores(q_tokens, store, k1=1.5, b=0.75):
    """纯 Python BM25。返回与 store['chunks'] 对齐的原始分数列表。"""
    docs = [_tokenize(c["text"]) for c in store["chunks"]]
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n
    df = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}
    scores = []
    for d in docs:
        freq = {}
        for t in d:
            freq[t] = freq.get(t, 0) + 1
        dl = len(d)
        s = 0.0
        for t in q_tokens:
            if t in freq:
                f = freq[t]
                s += idf.get(t, 0) * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


def _vec_scores(qe, store):
    out = []
    for c in store["chunks"]:
        v = cosine(qe, c["embedding"])
        out.append(max(0.0, v))
    return out


def _label(meta):
    if not meta:
        return "知识库"
    if meta.get("page"):
        return f"PPT第{meta['page']}页 {meta.get('title','')}"
    return meta.get("source") or meta.get("name") or "知识库"


def query(question, name="myme_kb", top_k=RETRIEVE_TOP_K):
    """纯向量检索（保留原接口，供 validate_e2e 等兼容使用）。"""
    store = load_kb(name)
    qe = ollama_embed(question, model=store.get("model", EMBED_MODEL))
    scored = [(cosine(qe, c["embedding"]), c) for c in store["chunks"]]
    scored.sort(key=lambda x: -x[0])
    out = []
    for s, c in scored[:top_k]:
        out.append({"text": c["text"], "score": round(s, 4),
                    "page": c["meta"].get("page"), "title": c["meta"].get("title"),
                    "source": c["meta"].get("source"), "label": _label(c["meta"])})
    return out


def hybrid_query(question, name="myme_kb", top_k=RETRIEVE_TOP_K,
                 alpha=0.6, threshold=0.0):
    """混合检索：向量(alpha) + BM25(1-alpha) 加权融合，再按向量阈值过滤。
    alpha=0.6 表示向量权重 0.6、BM25 权重 0.4（开放问答可调高 alpha，技术文档可调高 BM25）。
    threshold 为向量余弦阈值，低于此值视为不相关、直接丢弃（避免胡编）。"""
    store = load_kb(name)
    qe = ollama_embed(question, model=store.get("model", EMBED_MODEL))
    vec = _vec_scores(qe, store)
    bm = bm25_scores(_tokenize(question), store)
    return _fuse(question, store, vec, bm, top_k=top_k, alpha=alpha, threshold=threshold,
                 scope=("公有库" if name == "public"
                        else ("分身私有库" if str(name).startswith("person:") else "项目库")))


def _fuse(question, store, vec, bm, top_k=RETRIEVE_TOP_K, alpha=0.6,
          threshold=0.0, scope=""):
    """把向量分与 BM25 分融合并排序（hybrid_query 与 multi_query 共用）。"""
    if bm and max(bm) > min(bm):
        lo, hi = min(bm), max(bm)
        bmn = [(x - lo) / (hi - lo) for x in bm]
    else:
        bmn = [0.0] * len(bm)
    fused = [alpha * v + (1 - alpha) * b for v, b in zip(vec, bmn)]
    cand = [(f, v, c) for f, v, c in zip(fused, vec, store["chunks"]) if v >= threshold]
    cand.sort(key=lambda x: -x[0])
    out = []
    for f, v, c in cand[:top_k]:
        out.append({"text": c["text"], "score": round(f, 4), "vec": round(v, 4),
                    "page": c["meta"].get("page"), "title": c["meta"].get("title"),
                    "source": c["meta"].get("source"), "label": _label(c["meta"]),
                    "scope": scope, "kb": store.get("name", "")})
    return out


def multi_query(question, names, top_k=RETRIEVE_TOP_K, alpha=0.6,
                threshold=0.0, boost_private=1.15):
    """跨范围检索：项目库 + 公有库 + 分身私有库一起查，私有结果加权。

    为什么要加权：分身库里是这个人自己的用语习惯与专属资料，
    同一问题下它的答案应优先于公有常识，否则分身会"失去个性"。
    返回时按加权后的综合分统一排序，并保留 scope 字段供前端标注来源。
    """
    merged = []
    for n in (names or []):
        try:
            store = load_kb(n)
        except Exception:
            continue
        if not (store.get("chunks") or []):
            continue
        try:
            qe = ollama_embed(question, model=store.get("model", EMBED_MODEL))
            vec = _vec_scores(qe, store)
            bm = bm25_scores(_tokenize(question), store)
        except Exception:
            continue
        scope = ("公有库" if n == "public"
                 else ("分身私有库" if str(n).startswith("person:") else "项目库"))
        for r in _fuse(question, store, vec, bm, top_k=top_k, alpha=alpha,
                       threshold=threshold, scope=scope):
            if scope != "公有库":
                r["score"] = round(r["score"] * boost_private, 4)
            merged.append(r)
    merged.sort(key=lambda x: -x["score"])
    return merged[:top_k]


def list_scopes():
    """列出所有已存在的知识库范围及其规模，供「知识库范围管理」展示。"""
    out = []
    cands = [("public", "公有通用库", "IT / 水利 / 公文等通用常识，全机共享"),
             (KB_PROJECT_NAME, "项目私有库", "当前项目专属资料")]
    try:
        import persons as _persons
        for p in (_persons.load().get("persons") or []):
            cands.append(("person:" + p.get("id", ""), "分身 · " + (p.get("name") or p.get("id")),
                          "该分身的用语习惯与专属文档模板"))
    except Exception:
        pass
    for name, title, desc in cands:
        try:
            st = load_kb(name)
        except Exception:
            st = {}
        out.append({"name": name, "title": title, "desc": desc,
                    "chunks": len(st.get("chunks") or []),
                    "sources": len(st.get("sources") or {}),
                    "dir": kb_dir_for(name)})
    return out


def promote_to_public(src_name, limit=None, category="由私有提升"):
    """把私有库（项目 / 分身）的内容提升进公有库。

    只做「追加」，不做删除——提升是沉淀复用，不是搬家；
    已存在的分块按 text 去重，避免反复提升导致公有库膨胀。
    """
    src = load_kb(src_name)
    try:
        pub = load_kb("public")
    except Exception:
        pub = {"name": "public", "chunks": [], "sources": {}, "model": src.get("model", EMBED_MODEL)}
    pub.setdefault("chunks", [])
    pub.setdefault("sources", {})
    have = set(c.get("text", "") for c in pub["chunks"])
    added = 0
    for c in (src.get("chunks") or []):
        t = c.get("text", "")
        if not t or t in have:
            continue
        meta = dict(c.get("meta") or {})
        meta.setdefault("category", category)
        meta["promoted_from"] = src_name
        pub["chunks"].append({"text": t, "vec": c.get("vec"), "meta": meta})
        have.add(t)
        added += 1
        if limit and added >= limit:
            break
    for k, v in (src.get("sources") or {}).items():
        pub["sources"].setdefault(k, v)
    _save_store(pub)
    return {"added": added, "public_chunks": len(pub["chunks"])}


def format_context(results):
    """把检索结果拼成喂给大模型的上下文（含来源，便于溯源）。"""
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(f"[{r.get('label', '知识库')}]\n{r['text']}")
    return "\n\n".join(lines)


# ============================================================
# 调用层上下文压缩（检索之后、送模型之前）
# ============================================================
def _est_tokens(text):
    """粗略估算 token：中文按字、英文/数字按词（≈字数的 1.3 倍）。用于压缩预算与统计。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    words = len(re.findall(r"[a-zA-Z0-9]+", text))
    return int(cjk + words * 1.3)


def _score_sentence(s, qset, rel):
    """单句打分：问题关键词命中（越多越相关）+ 句长适中（过长降权）+ 所属块检索分加权。

    返回 0~1+，分数越高越该进入「压缩后上下文」。"""
    stoks = _tokenize(s)
    if not stoks:
        return 0.0
    uniq = set(stoks)
    hit = sum(1 for t in uniq if t in qset)
    cov = hit / max(1, len(uniq))
    # 句长：中短句信息密度高；> 80 字的长句多为铺垫/背景，适当降权
    ln = len(s)
    lenfac = 1.0 if ln <= 80 else max(0.5, 80.0 / ln)
    return (0.7 * cov + 0.3 * min(1.0, hit / 4.0)) * lenfac * (0.6 + 0.8 * float(rel or 0))


COMPRESS_SYSTEM = (
    "你是一个知识片段压缩助手。请根据用户问题，从检索到的知识片段中提取最相关的关键信息，"
    "输出精简后的上下文。只输出精简内容本身，不要任何解释或代码块标记。"
)

COMPRESS_PROMPT = (
    "用户问题：{question}\n\n"
    "以下是检索到的候选知识片段（每块带 [FragN] 标记，N 为来源编号）：\n"
    "{frags}\n\n"
    "要求：\n"
    "1. 只保留与问题直接相关的句子、数据、定义、步骤。\n"
    "2. 删除重复、背景铺垫、无关段落。\n"
    "3. 不改变事实，不编造内容。\n"
    "4. 输出精简后的上下文，长度控制在 {token} token 以内。\n"
    "5. 每个保留内容后面用 [FragN] 标注其来源编号（如「汛期前后需专项巡检。[Frag2]」）。"
)

# 中文高频虚词/停用字：单字切分时这些字极常见，若参与关键词命中会产生大量误匹配
# （如「这是…」中的「是」撞上问句里的「是」）。压缩打分前从问题词集合里剔除。
_STOP = set("的是在了我你他她它们这与那和或及不也应被等也都就很更要会能可把给到从对为以于其此个其各上中下前后内外的把被让使将已并而但若若因由")


def _filter_qset(qset):
    """剔除停用字，返回「内容词」集合（用于句子相关性打分，减少误命中）。"""
    return set(t for t in qset if t not in _STOP)


def _llm_compress_context(question, results, max_tokens):
    """LLM 精炼压缩：把候选片段编号后交给模型提取相关要点并保留来源标记。"""
    frags = []
    for i, r in enumerate(results[:12]):
        frags.append("[Frag%d] %s" % (i, r.get("text", "")))
    prompt = COMPRESS_PROMPT.format(
        question=question, frags="\n\n".join(frags), token=max_tokens)
    return llm_client.chat(
        [{"role": "system", "content": COMPRESS_SYSTEM},
         {"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=min(2200, max_tokens + 500))


def compress_context(question, results, max_tokens=None, use_llm=None):
    """调用层上下文压缩：检索后的候选块 → 提取最相关句子/要点 → 去重 → 标注来源 → 控制 token。

    默认走规则提取（离线、零依赖、保留 [来源 #chunk] 标注，便于溯源）；
    use_llm=True 先尝试 LLM 精炼（需本地模型在线），失败/空则回落规则提取。
    返回 (压缩后文本, 统计字典)。

    原则：存储层仍保留原始块（不在本函数压缩存储）；这里只在「送模型前」压缩调用层上下文。
    """
    max_tokens = max_tokens or KB_COMPRESS_TOKENS
    use_llm = (KB_COMPRESS_USE_LLM if use_llm is None else use_llm)
    full_text = "\n".join(r.get("text", "") for r in results)
    if not results:
        return "", {"orig_tokens": 0, "comp_tokens": 0, "kept": 0,
                    "dropped": 0, "method": "none"}
    if use_llm:
        try:
            txt = _llm_compress_context(question, results, max_tokens)
            if txt and txt.strip():
                return txt.strip(), {
                    "orig_tokens": _est_tokens(full_text),
                    "comp_tokens": _est_tokens(txt), "kept": -1, "dropped": -1,
                    "method": "llm"}
        except Exception as e:
            print("[kb] LLM 压缩失败，回落规则提取:", e)

    # —— 规则提取（主路径，离线可靠）——
    qset = _filter_qset(_tokenize(question))
    buckets = []  # (score, label, chunk_id, sentence)
    for i, r in enumerate(results):
        label = r.get("label") or "知识库"
        for s in _split_sentences(r.get("text", "")):
            buckets.append((_score_sentence(s, qset, r.get("score", 0)), label, i, s))
    buckets.sort(key=lambda x: -x[0])
    kept, used, seen, dropped = [], 0, set(), 0
    budget = max_tokens * 2  # 中文约 2 字符/token 预算（宽松）
    for _sc, label, cid, s in buckets:
        st = s.strip()
        if len(st) < 6:
            dropped += 1
            continue
        # 相关性地板：与问题无任何关键词重叠的句子直接丢弃（背景铺垫/无关段落），
        # 避免「块相关但句无关」的内容污染压缩后上下文。
        if _sc <= 0:
            dropped += 1
            continue
        if used + len(st) > budget:
            break
        key = st[:40]
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append((label, cid, st))
        used += len(st)
    # 还原为「按来源分组、组内按相关度」便于阅读与溯源
    groups, order = {}, []
    for label, cid, st in kept:
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append((cid, st))
    lines = []
    for label in order:
        for cid, st in groups[label]:
            lines.append("[来源 %s #%d]\n%s" % (label, cid, st))
    comp = "\n\n".join(lines)
    return comp, {"orig_tokens": _est_tokens(full_text),
                  "comp_tokens": _est_tokens(comp), "kept": len(kept),
                  "dropped": dropped, "method": "rule"}


# ============================================================
# 知识库重建（去重 / 优化 / 压缩，仅动索引与冗余，保留原文与向量）
# ============================================================
def _merge_src(keep, dup):
    """去重时保留更完整块的原文，但把另一块的来源哈希并入 src_all，保证溯源不丢。"""
    ds = dup.get("src")
    if ds:
        existing = keep.get("src_all") or ([keep["src"]] if keep.get("src") else [])
        if ds not in existing:
            existing.append(ds)
        keep["src_all"] = existing


def kb_stats(name="myme_kb"):
    """知识库统计：块数 / 来源数 / 字符数 / 近似 token / 向量覆盖 / 精确重复估计 / 文件大小。"""
    path = _store_path(name)
    if not os.path.exists(path):
        return {"exists": False, "name": name}
    store = load_kb(name)
    chunks = store.get("chunks", [])
    srcs = store.get("sources", {})
    chars = sum(len(c.get("text", "")) for c in chunks)
    with_vec = sum(1 for c in chunks if c.get("embedding"))
    from collections import Counter
    dup = Counter((c.get("text", "") or "").strip() for c in chunks)
    exact_dup = sum(v - 1 for v in dup.values() if v > 1)
    size = os.path.getsize(path)
    return {
        "exists": True, "name": name, "model": store.get("model", EMBED_MODEL),
        "chunks": len(chunks), "sources": len(srcs), "chars": chars,
        "tokens": _est_tokens("\n".join(c.get("text", "") for c in chunks)),
        "with_vec": with_vec, "exact_dup": exact_dup, "size": size,
        "size_mb": round(size / 1048576.0, 4),
    }


def rebuild_kb(name="myme_kb", dedup_exact=True, dedup_near=True,
               near_thr=None, drop_short=True, min_chars=None):
    """知识库重建：去重 + 优化 + 压缩，提高知识库质量。

    - 精确去重：文本完全相同的块只留一条，来源元数据合并。
    - 近重复去重：余弦相似度 > near_thr 视为重复，保留更长更完整的一块。
    - 丢弃低质：字符数 < min_chars 的零散/空块。
    - 完成后重建来源清单计数，并落盘。返回前后对比统计。

    关键：存储层保留原始文本与向量，只删冗余——不把知识库存成「压缩结果」，
    后续仍可溯源、仍可二次检索。
    """
    path = _store_path(name)
    if not os.path.exists(path):
        return {"ok": False, "error": "知识库不存在：%s" % name}
    near_thr = near_thr or KB_NEAR_DUP_THR
    min_chars = min_chars or KB_MIN_CHARS
    store = load_kb(name)
    chunks = store.get("chunks", [])
    before = len(chunks)
    src_before = len(store.get("sources", {}))

    kept, removed_exact, removed_near, removed_short = [], 0, 0, 0
    seen_text = {}
    # ① 精确去重
    for c in chunks:
        t = (c.get("text", "") or "").strip()
        if dedup_exact and t in seen_text:
            _merge_src(seen_text[t], c)
            removed_exact += 1
            continue
        seen_text[t] = c
        kept.append(c)
    # ② 近重复去重（需双方有向量）
    if dedup_near and len(kept) > 1:
        survivors = []
        for c in kept:
            ev = c.get("embedding")
            if not ev:
                survivors.append(c)
                continue
            dup_of = None
            for s in survivors:
                sv = s.get("embedding")
                if sv and cosine(ev, sv) > near_thr:
                    dup_of = s
                    break
            if dup_of is None:
                survivors.append(c)
            else:
                _merge_src(dup_of, c)
                removed_near += 1
        kept = survivors
    # ③ 丢弃低质短块
    if drop_short:
        new_kept = []
        for c in kept:
            if len((c.get("text", "") or "").strip()) < min_chars:
                removed_short += 1
                continue
            new_kept.append(c)
        kept = new_kept
    # ④ 重建来源清单计数（按保留块的实际 src 聚合，保证与数据一致）
    new_sources = {}
    for c in kept:
        h = c.get("src")
        if not h:
            continue
        rec = new_sources.setdefault(h, dict(store.get("sources", {}).get(h, {})))
        rec["n"] = rec.get("n", 0) + 1
    store["chunks"] = kept
    store["sources"] = new_sources
    store["rebuilt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    store["rebuilt_stats"] = {"before": before, "after": len(kept),
                              "removed_exact": removed_exact,
                              "removed_near": removed_near,
                              "removed_short": removed_short}
    _save_store(store)
    after = kb_stats(name)
    return {
        "ok": True, "name": name,
        "before": before, "after": len(kept),
        "removed_exact": removed_exact, "removed_near": removed_near,
        "removed_short": removed_short,
        "src_before": src_before, "src_after": len(new_sources),
        "after_stats": after,
    }


# ============================================================
# 知识库备份导出 / 导入（格式兼容：导出的 .mymekb 可原样导入还原）
# ============================================================
def _copy_stream(src, dst):
    """流式拷贝（分块），不整文件进内存。"""
    try:
        while True:
            buf = src.read(1024 * 1024)
            if not buf:
                break
            dst.write(buf)
    finally:
        try:
            dst.close()
        except Exception:
            pass
        try:
            src.close()
        except Exception:
            pass


def export_backup(name="myme_kb", out_path=None):
    """把 kb_data/ 整目录打包为 .mymekb（zip + manifest.json）。格式兼容、可再导入还原。

    打包的是「原始知识库文件本身」（含每块原文 + 向量 + 元数据），不做任何压缩变形，
    所以导入即还原；manifest 记录 schema/版本/模型/块数/来源，便于校验。"""
    from datetime import datetime
    kb_dir = config.DIR_KB
    if out_path is None:
        os.makedirs(config.DIR_TEST, exist_ok=True)
        out_path = os.path.join(config.DIR_TEST,
                                "myme_kb_%s%s"
                                % (time.strftime("%Y%m%d_%H%M%S"), KB_BACKUP_EXT))
    files = []
    if os.path.isdir(kb_dir):
        for fn in sorted(os.listdir(kb_dir)):
            fp = os.path.join(kb_dir, fn)
            if os.path.isfile(fp) and fn.endswith(".json"):
                files.append(fp)
    if not files:
        return {"ok": False, "error": "知识库为空，无可导出内容"}
    total_chunks, manifest_sources = 0, []
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
            total_chunks += len(d.get("chunks", []))
            for s in (d.get("sources", {}) or {}).values():
                manifest_sources.append(s)
        except Exception:
            pass
    manifest = {
        "schema": "mymekb", "version": 1,
        "app_version": getattr(config, "VERSION", ""),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kb_dir": os.path.basename(kb_dir),
        "files": [os.path.basename(f) for f in files],
        "chunk_total": total_chunks,
        "sources": manifest_sources,
    }
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",
                   json.dumps(manifest, ensure_ascii=False, indent=2))
        for fp in files:
            z.write(fp, "kb_data/" + os.path.basename(fp))
    size = os.path.getsize(out_path)
    return {"ok": True, "path": out_path, "name": os.path.basename(out_path),
            "size": size, "size_mb": round(size / 1048576.0, 4),
            "files": len(files), "chunks": total_chunks, "manifest": manifest}


def import_backup(backup_path, merge=True):
    """从 .mymekb 还原知识库到 kb_data/。

    merge=True：同名文件覆盖、其余保留（适合「合并多份备份 / 增量恢复」）；
    merge=False：先清空旧 kb_data/*.json 再导入（适合「整库还原」）。
    同时兼容「旧式单库 json」（直接是一个知识库 store）——按 .json 还原。
    返回还原结果与还原后统计。"""
    if not os.path.exists(backup_path):
        return {"ok": False, "error": "备份文件不存在"}
    # 兼容：直接是知识库 store json（不强制 zip）
    if not zipfile.is_zipfile(backup_path):
        try:
            d = json.load(open(backup_path, encoding="utf-8"))
            if isinstance(d, dict) and "chunks" in d:
                os.makedirs(config.DIR_KB, exist_ok=True)
                import shutil as _sh
                _sh.copy(backup_path, _store_path(d.get("name", "myme_kb")))
                return {"ok": True, "mode": "json",
                        "chunks": len(d.get("chunks", [])),
                        "stats": kb_stats(d.get("name", "myme_kb"))}
        except Exception as e:
            return {"ok": False, "error": "不是有效的 .mymekb 或知识库 json：%s" % e}
        return {"ok": False, "error": "文件不是有效的知识库备份"}
    os.makedirs(config.DIR_KB, exist_ok=True)
    restored, manifest = 0, None
    if not merge:
        for fn in os.listdir(config.DIR_KB):
            if fn.endswith(".json"):
                try:
                    os.remove(os.path.join(config.DIR_KB, fn))
                except Exception:
                    pass
    with zipfile.ZipFile(backup_path, "r") as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            nm = info.filename
            if nm == "manifest.json":
                try:
                    manifest = json.loads(z.read(nm).decode("utf-8"))
                except Exception:
                    pass
                continue
            if nm.startswith("kb_data/") and nm.endswith(".json"):
                target = os.path.join(config.DIR_KB, os.path.basename(nm))
                with open(target, "wb") as dst:
                    _copy_stream(z.open(nm), dst)
                restored += 1
    return {"ok": True, "mode": "zip", "restored": restored,
            "manifest": manifest, "stats": kb_stats()}


if __name__ == "__main__":
    fake = [{"idx": 1, "title": "水渠道维护",
             "text": "水渠道常规维护周期为每季度一次，汛期前后需额外专项巡检。",
             "notes": "汛前4月、汛后10月各一次。"}]
    b = build_kb(fake)
    print("build:", b)
    q = query("维护周期是多久？")
    print("向量检索:", format_context(q)[:200])
    h = hybrid_query("维护周期是多久？")
    print("混合检索:", [(r["label"], r["score"]) for r in h])
