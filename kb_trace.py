# -*- coding: utf-8 -*-
"""
kb_trace.py — 全链路追踪（吸收 WeKnora v0.8.2 的 Langfuse 可观测性）

Langfuse 做的事：每次问答的 token 消耗、检索路径、Agent 推理过程全部可视化，
出问题能精确定位是「检索不行」还是「模型不行」。

本机不装 Langfuse（还要外网），改成同等能力、零依赖的本地版：
每次问答/Agent/入库/产出都往 KP_ROOT/trace/trace.jsonl 追加一条，
界面可读最近记录、可按类型聚合 —— 够定位，也够复盘。

    kb_trace.start("qa", question="…")        → trace_id（上下文用）
    kb_trace.span(tid, "hybrid_query", n=8)   → 记一条子步骤（检索路径）
    kb_trace.finish(tid, status="ok", ...)    → 落盘
    kb_trace.log({...})                       → 一行搞定（不需要分步时）
    kb_trace.query(limit=20, kind="qa")       → 最近记录（倒序）
    kb_trace.stats()                          → 按类型聚合：次数/耗时/token

CLI:
    python kb_trace.py                 # 统计
    python kb_trace.py --tail 5        # 看最近 5 条
"""

import os
import json
import time

import config

def trace_dir():
    d = os.path.join(getattr(config, "KP_ROOT", None) or config.DIR_DATA, "trace")
    os.makedirs(d, exist_ok=True)
    return d


def trace_file():
    return os.path.join(trace_dir(), "trace.jsonl")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def est_tokens(text):
    """本机模型大多不下发 usage，按中文≈1 token/1.6 字、英文≈1 token/4 字符粗估。"""
    text = text or ""
    zh = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    en = len(text) - zh
    return int(zh / 1.6 + en / 4) + 1


def start(kind, question="", **kw):
    tid = time.strftime("%Y%m%d%H%M%S") + "_%06d" % int(time.time() * 1000 % 1000000)
    rec = {"ts": _now(), "kind": kind, "id": tid, "question": question[:200],
           "spans": [], "status": "running", "secs": 0.0}
    rec.update({k: v for k, v in kw.items() if k != "spans"})
    return tid


def span(tid, name, **kw):
    """记一条链路里的子步骤（一次检索、一次模型调用、一次抓取…）。"""
    path = trace_file()
    rec = {"ts": _now(), "kind": "span", "id": tid, "span": name}
    rec.update(kw)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def finish(tid, status="ok", tokens=None, **kw):
    rec = {"ts": _now(), "kind": "trace", "id": tid, "status": status,
           "secs": round(time.time() - float(kw.pop("t0", time.time())), 2)}
    rec.update(kw)
    if tokens is None:
        # 本机模型多数不下发 usage，就按答案长度估一个（总比显示 0 让人误以为没花钱）
        rec["tokens"] = est_tokens(rec.get("answer") or "") if rec.get("answer") else 0
    elif isinstance(tokens, dict):
        rec["tokens"] = tokens.get("total", 0)
        rec["tokens_detail"] = tokens
    else:
        rec["tokens"] = tokens
    with open(trace_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def log(entry):
    """一行落盘。entry 至少给 kind；常用字段：question/answer/steps/tokens/secs/status。"""
    rec = dict(entry or {})
    rec.setdefault("ts", _now())
    rec.setdefault("tokens", 0)
    if isinstance(rec.get("answer"), str):
        rec["tokens"] = rec.get("tokens") or est_tokens(rec["answer"])
    if entry and entry.get("answer_len") is None and isinstance(rec.get("answer"), str):
        rec["answer_len"] = len(rec["answer"])
    with open(trace_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def query(limit=20, kind=None):
    p = trace_file()
    if not os.path.exists(p):
        return []
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if kind and r.get("kind") != kind:
                continue
            out.append(r)
    out.reverse()
    return out[:limit]


def stats():
    recs = query(limit=5000, kind="trace") + query(limit=5000, kind="agent")
    if not recs:
        return {"count": 0}
    groups = {}
    for r in recs:
        k = r.get("kind") or "? "
        g = groups.setdefault(k, {"count": 0, "secs": 0.0, "tokens": 0, "ok": 0,
                                  "err": 0, "last": ""})
        g["count"] += 1
        g["secs"] = round(g["secs"] + float(r.get("secs") or 0), 1)
        g["tokens"] += int(r.get("tokens") or 0)
        if r.get("status") in ("ok", "done"):
            g["ok"] += 1
        else:
            g["err"] += 1
        g["last"] = r.get("ts", "")
    for g in groups.values():
        g["avg_secs"] = round(g["secs"] / max(1, g["count"]), 1)
    return {"count": len(recs), "groups": groups,
            "trace_file": trace_file(), "bytes": os.path.getsize(trace_file())}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="知识库链路追踪（本地版 Langfuse）")
    ap.add_argument("--tail", type=int, default=0, help="看最近 N 条")
    ap.add_argument("--kind", default="", help="qa / agent / ingest / export / wiki")
    a = ap.parse_args()
    if a.tail:
        for r in query(limit=a.tail, kind=a.kind or None):
            print("[%s] %s %s | %.1fs | %s" % (
                r.get("ts"), r.get("kind"), r.get("id", ""), float(r.get("secs") or 0),
                (r.get("question") or r.get("action") or r.get("answer") or "")[:80]))
    else:
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
