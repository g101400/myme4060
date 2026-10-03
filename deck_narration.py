# -*- coding: utf-8 -*-
"""
myme.deck_narration — 讲解文本（口播稿）的解析与清洗。

需求背景：
  导入 PPT 生成视频讲解时，PPT 是必选项，讲解文本是**可选项**——
  用户手写了讲稿就按讲稿讲；没写（或只写了部分页）就按幻灯片内容自动生成。

因此本模块只做两件事：
  1) parse_narration_text：把各种写法的讲稿解析成 {页码: 讲解词}
     - 支持「## 第 1 页」「第1页」「## 1.」「### P1」等带页码的标题
     - 支持无页码、用 --- 分隔的旧稿（按顺序配页）
     - 支持纯分段文本（按顺序 1..N 配页）
  2) clean_speech：把 markdown 记号洗掉，让 TTS 读起来自然（不念星号、井号、图片链接）

解析只认"顺序"不认"猜"：能明确取到页码就按页码，取不到就按出现顺序配页，
配不上的部分直接丢弃并计数，由调用方决定是自动生成还是告警。
"""
import re

# 带页码的标题：## 第 1 页 / 第1页：概述 / ### 1. 开场 / ## P3 结尾
RE_HEAD_NUM = re.compile(
    r"(?m)^\s{0,3}#{1,6}\s*(?:第\s*)?(\d{1,4})\s*(?:页|张)?\b[^\n]*$"
)
# 行内的「第 1 页」写法（标题里没有 # 号时）
RE_INLINE_NUM = re.compile(r"第\s*(\d{1,4})\s*页")
RE_HR = re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def clean_speech(text):
    """把 markdown / 版式记号洗成可朗读的纯文本。"""
    if not text:
        return ""
    t = text
    # 去掉图片与链接，保留可读文字
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # 去掉标题井号、引用符、列表符、粗体斜体、行内代码
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)
    t = re.sub(r"(?m)^\s*>\s*", "", t)
    t = re.sub(r"(?m)^\s*[-*+]\s+", "", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = t.replace("`", "")
    # 表格竖线变停顿
    t = t.replace("|", "，")
    # 折叠空白
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _split_blocks(text):
    """按空行分段，返回去空的块列表。"""
    return [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]


def parse_narration_text(text):
    """把讲解文本解析为 {idx: 讲解词}。

    返回 (mapping, stats)；stats 说明用了哪种解析方式、丢弃了多少块，便于上报告警。
    """
    mapping = {}
    stats = {"mode": "none", "blocks": 0, "dropped": 0}
    if not text or not text.strip():
        return mapping, stats

    # 方式一：按 Markdown 标题切分，标题里带页码
    parts = RE_HEAD_NUM.split(text)
    if len(parts) >= 3:                       # 形如 [前文, 页号1, 正文1, 页号2, 正文2...]
        stats["mode"] = "heading"
        head = parts[0].strip()
        if head:
            stats["dropped"] += 1             # 标题之前的引言不属于任何一页
        it = iter(range(1, len(parts), 2))
        for i in it:
            try:
                idx = int(parts[i])
            except ValueError:
                stats["dropped"] += 1
                continue
            body = clean_speech(parts[i + 1] if i + 1 < len(parts) else "")
            if body:
                mapping[idx] = body
                stats["blocks"] += 1
        if mapping:
            return mapping, stats

    # 方式二：行内「第 N 页」开头的段落
    blocks = _split_blocks(text)
    hit = 0
    for b in blocks:
        m = RE_INLINE_NUM.search(b.splitlines()[0][:40]) if b else None
        if m:
            # 去掉正文开头残留的「第 N 页」标记，避免 TTS 把页码念出来
            body = clean_speech(RE_INLINE_NUM.sub("", b, count=1))
            if body:
                mapping[int(m.group(1))] = body
                hit += 1
    if hit:
        stats.update({"mode": "inline", "blocks": hit, "dropped": len(blocks) - hit})
        return mapping, stats

    # 方式三：--- 分隔的旧稿（按出现顺序配页）
    segs = [s.strip() for s in RE_HR.split(text) if s.strip()]
    if len(segs) > 1:
        stats.update({"mode": "hrule", "blocks": len(segs)})
        for i, s in enumerate(segs, 1):
            body = clean_speech(s)
            if body:
                mapping[i] = body
        return mapping, stats

    # 方式四：纯分段文本，按出现顺序 1..N 配页
    stats.update({"mode": "sequence", "blocks": len(blocks)})
    for i, b in enumerate(blocks, 1):
        body = clean_speech(b)
        if body:
            mapping[i] = body
    return mapping, stats


def plan_narrations(slides, mapping):
    """为每一页决定讲解词来源。

    返回 [(idx, title, text, source)]，source 为 "文本"（用户讲稿）或 None（需自动生成）。
    mapping 为空或某页缺失时，该页标记为需生成，由调用方用 LLM 补齐——
    这样"只写了部分页"的讲稿也能用，不会整篇退化。
    """
    plan = []
    for i, sl in enumerate(slides, 1):
        idx = sl.get("idx", i)
        txt = (mapping or {}).get(idx) or (mapping or {}).get(i)
        plan.append((idx, sl.get("title", ""), txt or "", "文本" if txt else None))
    return plan


def read_narration_file(path):
    """读取讲解文本文件（.md/.txt），自动处理编码。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return open(path, encoding=enc).read()
        except UnicodeDecodeError:
            continue
    return open(path, encoding="utf-8", errors="ignore").read()
