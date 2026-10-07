# -*- coding: utf-8 -*-
"""kb_export —— 知识库 → 多项产物（本地离线版）

吸收 qiaomu 的「自然语言 → 产出意图映射表」，把其中的
audio / slide-deck / mind-map / quiz / report / flashcards / data-table
七类意图，全部改由**本机知识库检索 + 本地模型 + 本机既有管道**产出：

    notebooklm generate audio      → narrate.narrate_slides（TTS 本地合成）
    notebooklm generate slide-deck → make_ppt.gen_outline + pptx_write
    notebooklm generate mind-map   → Markdown 树 + Mermaid mindmap
    notebooklm generate quiz       → 选择题 JSON
    notebooklm generate flashcards → 记忆卡片
    notebooklm generate data-table → Markdown 表格 + CSV
    notebooklm generate report     → Markdown + docx/pdf（doc_write）

统一入口：
    kb_export.export("report", topic="智慧水利三端四平台", kb="myme_kb")
"""

import os
import re
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config

DIR_EXPORT = os.path.join(config.KP_ROOT, "exports")
os.makedirs(DIR_EXPORT, exist_ok=True)

# 意图映射：把用户自然语言里的说法归一到七种产物
INTENT = {
    "audio": "podcast", "播客": "podcast", "音频": "podcast", "语音": "podcast",
    "slide-deck": "pptx", "ppt": "pptx", "幻灯片": "pptx", "演示": "pptx",
    "slide_deck": "pptx", "mind-map": "mindmap", "思维导图": "mindmap", "脑图": "mindmap",
    "quiz": "quiz", "出题": "quiz", "测验": "quiz", "试题": "quiz",
    "report": "report", "报告": "report", "总结": "report", "文档": "report",
    "flashcards": "flashcards", "闪卡": "flashcards", "记忆卡片": "flashcards",
    "data-table": "table", "表格": "table", "数据表": "table",
}

PROMPTS = {}
PROMPTS["quiz"] = (
    "你是出卷老师。只依据下面【资料片段】出题，不要联网、不要臆造。\n"
    "输出严格 JSON：{{\"title\":\"...\",\"questions\":[{{\"q\":\"题干\",\"options\":[\"A..\",\"B..\",\"C..\",\"D..\"],"
    "\"answer\":\"B\",\"explain\":\"一句话解析\"}}]}}\n"
    "共 8 题，覆盖不同模块，4 个选项各不重复。\n\n【资料片段】\n{ctx}")
PROMPTS["flashcards"] = (
    "你做记忆卡片。只依据【资料片段】，输出严格 JSON：\n"
    "{{\"title\":\"...\",\"cards\":[{{\"front\":\"问题或关键词\",\"back\":\"答案，不超过80字\"}}]}}\n"
    "共 15 张，聚焦易混点、数字、专有名词。\n\n【资料片段】\n{ctx}")
PROMPTS["mindmap"] = (
    "你把资料整理成思维导图文本。只依据【资料片段】，输出严格 JSON：\n"
    "{{\"title\":\"...\",\"root\":\"中心主题\",\"branches\":[{{\"name\":\"分支名\",\"children\":[\"...\", \"...\"]}}]}}\n"
    "4-6 个分支，每分支 3-5 个子节点，节点要短（<=10字）。\n\n【资料片段】\n{ctx}")
PROMPTS["table"] = (
    "你做数据表。只依据【资料片段】，输出严格 JSON：\n"
    "{{\"title\":\"...\",\"headers\":[\"列1\",\"列2\",\"列3\"],\"rows\":[[...],[...]]}}\n"
    "5-10 行，数字要来自资料，不要编。\n\n【资料片段】\n{ctx}")
PROMPTS["report"] = (
    "你写公开汇报材料。只依据【资料片段】，不得联网查证、不得编造。\n"
    "输出 Markdown：# 标题 / ## 一、背景 / ## 二、现状与数据 / ## 三、问题诊断 / "
    "## 四、措施建议 / ## 五、下一步。每节 3-6 句，关键数字标出来源。\n\n【资料片段】\n{ctx}")


# ==================================================================
# 取料
# ==================================================================
def gather(topic, kb="myme_kb", top_k=10, max_chars=9000):
    """从知识库检索围绕主题的片段，拼成给模型的原料。"""
    try:
        import knowledge_base
        res = knowledge_base.hybrid_query(topic, name=kb, top_k=top_k)
    except Exception as e:
        return "", "知识库检索失败：%s" % e
    parts = []
    for i, r in enumerate(res, 1):
        label = r.get("label") or r.get("source") or "来源%d" % i
        bc = r.get("breadcrumb")
        head = "%s%s" % (bc + " · " if bc else "", label)
        parts.append("【片段%d · %s】\n%s" % (i, head, (r.get("text") or "")[:1600]))
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[:max_chars]
    return blob, ""


def _chat(prompt, temperature=0.3, max_tokens=2200):
    """走本机 Ollama。llm_client.chat 收 messages 列表；没有就退回 qa_brain。"""
    try:
        from llm_client import chat as _chat_fn
        return _chat_fn([{"role": "user", "content": prompt}],
                        temperature=temperature, max_tokens=max_tokens)
    except Exception as e1:
        try:
            import qa_brain
            return qa_brain.ollama_chat(prompt, temperature=temperature, max_tokens=max_tokens)
        except Exception as e2:
            raise RuntimeError("本地聊天接口不可用：%s / %s" % (e1, e2))


def _json_from(text):
    """从模型输出里抠出 JSON（可能带 ```json 围栏）。"""
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}\s*$", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def _safe(name):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "导出") )[:60]


# ==================================================================
# 各产物
# ==================================================================
def _export_report(topic, ctx, meta, outdir):
    txt = _chat(PROMPTS["report"].replace("{ctx}", ctx), max_tokens=2600)
    title = "%s · 分析报告" % topic
    md = "# %s\n\n> 生成时间：%s　|　来源：本地知识库（%d 条片段）\n\n%s\n" % (
        title, time.strftime("%Y-%m-%d %H:%M"), meta.get("hits", 0), txt)
    p_md = os.path.join(outdir, _safe(topic) + "_报告.md")
    open(p_md, "w", encoding="utf-8").write(md)
    out = {"md": p_md}
    try:
        import doc_write
        blocks = [{"type": "h1", "text": line.lstrip("# ").strip()}
                  if line.startswith("# ") else
                  {"type": "h2", "text": line.lstrip("# ").strip()}
                  if line.startswith("## ") else
                  {"type": "p", "text": line}
                  for line in md.split("\n") if line.strip()]
        blocks = [b for b in blocks if b.get("text", "").strip()]
        r = doc_write.write_doc(_safe(topic) + "_报告", title, blocks, outdir, formats=("docx",))
        out["docx"] = r.get("docx", "")
    except Exception as e:
        out["docx_error"] = str(e)
    return out


def _export_quiz(topic, ctx, meta, outdir):
    d = _json_from(_chat(PROMPTS["quiz"].replace("{ctx}", ctx)))
    qs = d.get("questions") or []
    lines = ["# %s · 测验题" % topic, ""]
    for i, q in enumerate(qs, 1):
        lines.append("## %d. %s" % (i, q.get("q", "")))
        for opt in (q.get("options") or []):
            lines.append("- %s" % opt)
        lines.append("- **答案**：%s" % q.get("answer", ""))
        if q.get("explain"):
            lines.append("- 解析：%s" % q["explain"])
        lines.append("")
    p = os.path.join(outdir, _safe(topic) + "_测验.md")
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    return {"md": p, "count": len(qs)}


def _export_flashcards(topic, ctx, meta, outdir):
    d = _json_from(_chat(PROMPTS["flashcards"].replace("{ctx}", ctx)))
    cards = d.get("cards") or []
    lines = ["# %s · 记忆卡片" % topic, ""]
    for i, c in enumerate(cards, 1):
        lines.append("## %d. %s" % (i, c.get("front", "")))
        lines.append("- %s" % c.get("back", ""))
        lines.append("")
    p = os.path.join(outdir, _safe(topic) + "_闪卡.md")
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    return {"md": p, "count": len(cards)}


def _export_mindmap(topic, ctx, meta, outdir):
    d = _json_from(_chat(PROMPTS["mindmap"].replace("{ctx}", ctx)))
    root = d.get("root") or topic
    lines = ["# %s · 思维导图" % topic, ""]
    lines.append("## 中心主题")
    lines.append("- **%s**" % root)
    lines.append("")
    lines.append("```mermaid")
    lines.append("mindmap")
    lines.append("  root((%s))" % root)
    for b in (d.get("branches") or []):
        lines.append("    %s" % b.get("name", ""))
        for c in (b.get("children") or []):
            lines.append("      %s" % c)
    lines.append("```")
    lines.append("")
    lines.append("## 文本大纲")
    lines.append("- %s" % root)
    for b in (d.get("branches") or []):
        lines.append("  - %s" % b.get("name", ""))
        for c in (b.get("children") or []):
            lines.append("    - %s" % c)
    p = os.path.join(outdir, _safe(topic) + "_思维导图.md")
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    return {"md": p, "branches": len(d.get("branches") or [])}


def _export_table(topic, ctx, meta, outdir):
    d = _json_from(_chat(PROMPTS["table"].replace("{ctx}", ctx)))
    heads, rows = d.get("headers") or [], d.get("rows") or []
    lines = ["# %s · 数据表" % topic, "", "| " + " | ".join(heads) + " |",
             "|" + "|".join(["---"] * max(1, len(heads))) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    p_md = os.path.join(outdir, _safe(topic) + "_数据表.md")
    open(p_md, "w", encoding="utf-8").write("\n".join(lines))
    out = {"md": p_md, "rows": len(rows)}
    try:
        import csv
        p_csv = os.path.join(outdir, _safe(topic) + "_数据表.csv")
        with open(p_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if heads:
                w.writerow(heads)
            for r in rows:
                w.writerow(r)
        out["csv"] = p_csv
    except Exception as e:
        out["csv_error"] = str(e)
    return out


def _export_pptx(topic, ctx, meta, outdir, pages=8, style="商务简洁", lang="中文"):
    """PPT：直接走本机既有管道（模型+知识库生成大纲 → pptx_write 写盘）。"""
    try:
        import make_ppt
        # gen_outline 返回 (slides, meta)，别把元组直接喂给写盘
        res = make_ppt.gen_outline(topic, style=style, lang=lang, pages=pages,
                                   use_kb=True, kb_name="myme_kb")
        outline = res[0] if isinstance(res, tuple) else res
        meta_src = res[1] if isinstance(res, tuple) else {}
    except Exception as e:
        return {"error": "PPT 大纲生成失败：%s" % e}
    out = {"outline": outline}
    try:
        import pptx_write
        p = os.path.join(outdir, _safe(topic) + ".pptx")
        # gen_outline 给的是 [{"title":..,"bullets":[..]}]，直接喂给既有写盘管道
        pptx_write.write_pptx(p, outline)
        out["pptx"] = p
    except Exception as e:
        out["pptx_error"] = str(e)
    return out


def _export_podcast(topic, ctx, meta, outdir, lang="中文", words=200):
    """播客：先出讲稿，再交给本机 TTS 合成。"""
    prompt = ("你写播客讲稿。只依据【资料片段】，口语化、有节奏、适合听，"
              "开头一句钩子，中段分 3 段，结尾一句收束。约 %d 字。\n\n【资料片段】\n%s" % (words * 5, ctx))
    script = _chat(prompt, max_tokens=2600)
    p_md = os.path.join(outdir, _safe(topic) + "_讲稿.md")
    open(p_md, "w", encoding="utf-8").write("# %s · 播客讲稿\n\n%s\n" % (topic, script))
    out = {"md": p_md, "chars": len(script)}
    try:
        import narrate
        lines = [l.strip() for l in script.split("\n")
                 if len(l.strip()) > 12 and not l.startswith("#")]
        batches = [lines[i:i + 4] for i in range(0, len(lines), 4)] or [[script[:800]]]
        paths = []
        for i, b in enumerate(batches, 1):
            p = os.path.join(outdir, "%s_p%02d.wav" % (_safe(topic), i))
            narrate.synth_one(" ".join(b), lang, p)
            paths.append(p)
        if len(paths) > 1:
            final = os.path.join(outdir, _safe(topic) + ".wav")
            narrate.concat_audio(paths, final)
            out["wav"] = final
        else:
            out["wav"] = paths[0] if paths else ""
    except Exception as e:
        out["audio_error"] = str(e)
    return out


KIND_FN = {
    "report": _export_report, "quiz": _export_quiz,
    "flashcards": _export_flashcards, "mindmap": _export_mindmap,
    "table": _export_table, "pptx": _export_pptx, "podcast": _export_podcast,
}


def export(kind, topic, kb="myme_kb", outdir=None, **opt):
    """统一导出入口。kind 支持 report/pptx/podcast/mindmap/quiz/flashcards/table。"""
    t0 = time.time()
    kind = INTENT.get(str(kind).strip().lower(), kind)
    if kind not in KIND_FN:
        return {"ok": False, "error": "不支持的产出类型：%s（可选 %s）"
                                      % (kind, "/".join(sorted(KIND_FN)))}
    outdir = outdir or DIR_EXPORT
    os.makedirs(outdir, exist_ok=True)
    ctx, err = gather(topic, kb=kb)
    if err:
        return {"ok": False, "error": err, "kind": kind, "topic": topic}
    if len(ctx) < 80:
        return {"ok": False, "error": "知识库里没有检索到与「%s」相关的片段，"
                                      "先去「多源导入台」把资料搬进来" % topic,
                "kind": kind, "topic": topic}
    import inspect
    opt = dict(opt)
    fn = KIND_FN[kind]
    # 只传目标函数真认得的参数（report 不该收到 pages，pptx 不该收 max_chars…）
    try:
        params = inspect.signature(fn).parameters
        opt = {k: v for k, v in opt.items() if k in params}
    except Exception:
        pass
    try:
        res = fn(topic, ctx, {"hits": len(ctx)}, outdir, **opt)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "kind": kind, "topic": topic, "secs": round(time.time() - t0, 1)}
    res.update({"ok": True, "kind": kind, "topic": topic,
                "out": outdir, "secs": round(time.time() - t0, 1)})
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser(description="知识库 → 多项产物")
    ap.add_argument("kind", help="report/pptx/podcast/mindmap/quiz/flashcards/table")
    ap.add_argument("topic")
    ap.add_argument("--kb", default="myme_kb")
    a = ap.parse_args()
    print(json.dumps(export(a.kind, a.topic, kb=a.kb), ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
