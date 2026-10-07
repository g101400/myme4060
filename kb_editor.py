# -*- coding: utf-8 -*-
"""
kb_editor.py — 分块可编辑 + 版本快照 + diff + 一键回滚（吸收 WeKnora v0.8.2 的 Chunk 编辑）

WeKnora 里 Chunk 是「可运营资产」：你在界面上直接改检索用的分块内容，有**版本快照**和
**diff 对比**，能一键回滚，改完自动重建索引。它解决的是检索侧一个很现实的毛病：
自动切出来的块本身就有噪声（把「三端四平台」切成两半、正文里混进页脚），
而这类噪声只有人一眼能看出来，改掉它比换模型便宜得多。

myme 这里做成本地版：

    kb_editor.list(kb)                 列出可编辑分块（可按关键词筛）
    kb_editor.get(kb, idx)             看某一块 + 它的版本历史
    kb_editor.update(kb, idx, text)    存新版本（旧版本自动留快照），重算向量
    kb_editor.diff(kb, idx, v)         和指定历史版本做 diff
    kb_editor.rollback(kb, idx, v)     一键回滚 + 重建索引
    kb_editor.reindex(kb, idx=None)    重算向量（改文本后必做，否则检索命中的是旧向量）

版本快照直接存在 chunk 的 "revs" 字段里（store 落盘的同一个 json），不另建文件，
备份/迁移时天然带上。

CLI:
    python kb_editor.py myme_kb --search 三端四平台
    python kb_editor.py myme_kb --idx 3 --set "…新文本…"
    python kb_editor.py myme_kb --idx 3 --rollback 1
"""

import os
import re
import json
import time
import difflib

# ============================================================
# 存储
# ============================================================
def _load(kb="myme_kb"):
    import knowledge_base
    return knowledge_base.load_kb(kb)


def _save(store, kb="myme_kb"):
    import knowledge_base
    knowledge_base._save_store(store)


def _reembed(chunk, store=None):
    """按 kb_chunking.embedding_content 的口径重算该块向量（改了文本不重算 = 白改）。"""
    try:
        import knowledge_base as kbmod
        if kbmod.KB_PARENT_CHILD == "1":
            try:
                import kb_chunking
                text = kb_chunking.embedding_content(chunk)
            except Exception:
                text = chunk.get("text", "")
        else:
            text = chunk.get("text", "")
        chunk["embedding"] = kbmod.ollama_embed(text, model=store.get("model")
                                                if store else kbmod.EMBED_MODEL)
        return True
    except Exception as e:
        return "重算向量失败：%s" % e


# ============================================================
# 读写
# ============================================================
def list_chunks(kb="myme_kb", query="", limit=50):
    """列出分块。query 非空时先用文本包含粗筛（不重跑向量，快）。"""
    store = _load(kb)
    chunks = store.get("chunks") or []
    out = []
    q = (query or "").strip()
    for i, c in enumerate(chunks):
        text = c.get("text") or ""
        if q and q not in text and q not in (c.get("breadcrumb") or ""):
            continue
        meta = c.get("meta") or {}
        out.append({
            "idx": i, "sid": c.get("sid") or ("c%d" % i),
            "label": c.get("label") or meta.get("source") or "知识库",
            "breadcrumb": c.get("breadcrumb") or "",
            "page": meta.get("page"),
            "chars": len(text),
            "revs": len(c.get("revs") or []),
            "preview": text[:120].replace("\n", " "),
        })
        if len(out) >= limit:
            break
    return {"kb": kb, "total": len(chunks), "items": out}


def get(kb="myme_kb", idx=0):
    store = _load(kb)
    chunks = store.get("chunks") or []
    if idx < 0 or idx >= len(chunks):
        return {"error": "没有这个分块（库里共 %d 块）" % len(chunks)}
    c = chunks[idx]
    revs = c.get("revs") or []
    return {"idx": idx, "label": c.get("label") or "", "breadcrumb": c.get("breadcrumb") or "",
            "text": c.get("text", ""), "chars": len(c.get("text") or ""),
            "revs": [{"v": r.get("v"), "at": r.get("at"), "note": r.get("note", ""),
                      "chars": len(r.get("text") or "")} for r in revs]}


def update(kb="myme_kb", idx=0, text=None, note="", reindex=True):
    """保存新版本：旧版本进 revs 快照 → 覆盖正文 → 重算向量。"""
    if text is None:
        return {"error": "没给新文本"}
    store = _load(kb)
    chunks = store.get("chunks") or []
    if idx < 0 or idx >= len(chunks):
        return {"error": "没有这个分块（库里共 %d 块）" % len(chunks)}
    c = chunks[idx]
    new = text.rstrip()
    if (c.get("text") or "") == new:
        return {"kb": kb, "idx": idx, "changed": 0, "revs": len(c.get("revs") or [])}
    revs = c.get("revs") or []
    revs.append({"v": len(revs) + 1, "text": c.get("text") or "", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "note": note or "人工编辑"})
    c["revs"] = revs
    c["edited_at"] = revs[-1]["at"]
    c["text"] = new
    msg = _reembed(c, store)
    _save(store, kb)
    return {"kb": kb, "idx": idx, "changed": len(new) - len(c.get("text") or ""),
            "revs": len(revs), "latest": revs[-1], "reindex": msg if isinstance(msg, str) else "ok"}


def diff(kb="myme_kb", idx=0, v=0):
    """和某个历史版本做 diff（v=0 表示默认与最新版本比，即看自己改了什么）。"""
    store = _load(kb)
    chunks = store.get("chunks") or []
    if idx >= len(chunks):
        return {"error": "没有这个分块"}
    c = chunks[idx]
    cur = (c.get("text") or "").splitlines()
    revs = c.get("revs") or []
    if not revs:
        return {"error": "这一块还没有历史版本（先编辑一次才会有快照）"}
    if v <= 0:
        v = len(revs)
    old = revs[v - 1].get("text", "").splitlines()
    lines = difflib.unified_diff(old, cur, fromfile="版本%d（%s）" % (v, revs[v - 1].get("at", "")),
                                 tofile="当前", lineterm="", n=2)
    return {"idx": idx, "version": v, "changed": difflib.SequenceMatcher(
        None, old, cur).ratio(), "diff": "\n".join(lines)}


def rollback(kb="myme_kb", idx=0, v=1, reindex=True):
    """回滚到某个历史版本（同样是「改」，所以新版本照样进快照，可反复回退）。"""
    store = _load(kb)
    chunks = store.get("chunks") or []
    if idx >= len(chunks):
        return {"error": "没有这个分块"}
    c = chunks[idx]
    revs = c.get("revs") or []
    if not (1 <= v <= len(revs)):
        return {"error": "版本 %d 不存在（当前 %d 个版本）" % (v, len(revs))}
    target = revs[v - 1]
    revs.append({"v": len(revs) + 1, "text": c.get("text") or "",
                 "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": "回滚前自动快照"})
    c["revs"] = revs
    c["text"] = target.get("text", "")
    msg = _reembed(c, store)
    _save(store, kb)
    return {"kb": kb, "idx": idx, "rolled_back_to": v, "revs": len(revs),
            "reindex": msg if isinstance(msg, str) else "ok"}


def reindex(kb="myme_kb", idx=None):
    """重建索引：默认全库重算向量（慢）；只给 idx 就只重算那几块。"""
    store = _load(kb)
    chunks = store.get("chunks") or []
    if idx is None:
        targets = list(range(len(chunks)))
    else:
        targets = idx if isinstance(idx, (list, tuple)) else [idx]
    ok, fail = 0, []
    for i in targets:
        if i >= len(chunks):
            fail.append("越界:%s" % i)
            continue
        msg = _reembed(chunks[i], store)
        if msg is True:
            ok += 1
        else:
            fail.append("%d:%s" % (i, msg))
    _save(store, kb)
    return {"kb": kb, "reindexed": ok, "failed": fail, "total": len(chunks)}


def stats(kb="myme_kb"):
    store = _load(kb)
    chunks = store.get("chunks") or []
    edited = [c for c in chunks if c.get("revs")]
    return {"kb": kb, "chunks": len(chunks), "edited": len(edited),
            "revs": sum(len(c.get("revs") or []) for c in chunks)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="分块可编辑 + 版本 diff + 回滚")
    ap.add_argument("kb", nargs="?", default="myme_kb")
    ap.add_argument("--search", default="", help="按关键词筛分块")
    ap.add_argument("--idx", type=int, default=0)
    ap.add_argument("--get", action="store_true")
    ap.add_argument("--set", default="", help="新文本（--file 时忽略）")
    ap.add_argument("--file", default="", help="从文件读新文本")
    ap.add_argument("--note", default="")
    ap.add_argument("--diff", type=int, default=0)
    ap.add_argument("--rollback", type=int, default=0)
    ap.add_argument("--reindex", action="store_true")
    a = ap.parse_args()

    if a.reindex:
        print(json.dumps(reindex(a.kb), ensure_ascii=False))
    elif a.rollback:
        print(json.dumps(rollback(a.kb, a.idx, a.rollback), ensure_ascii=False))
    elif a.diff:
        print(diff(a.kb, a.idx, a.diff).get("diff", ""))
    elif a.get:
        print(json.dumps(get(a.kb, a.idx), ensure_ascii=False, indent=2))
    elif a.set or a.file:
        txt = a.set
        if a.file:
            with open(a.file, "r", encoding="utf-8") as f:
                txt = f.read()
        print(json.dumps(update(a.kb, a.idx, txt, note=a.note), ensure_ascii=False, indent=2))
    else:
        r = list_chunks(a.kb, query=a.search)
        print(json.dumps(r, ensure_ascii=False, indent=2))
