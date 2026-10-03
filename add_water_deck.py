# -*- coding: utf-8 -*-
"""
把《水利一张图APP介绍_内部培训》PPT 装入 myme 数字分身，作为当前活动讲解稿。

流程：
  1. 备份当前 slides.json（旧 AI 演示稿）→ slides_ai_demo_backup.json
  2. 拷贝 PPTX 到 sample_ppt/（与既有演示稿同目录）
  3. pptx_parse 解析标题/正文/备注/内嵌图
  4. slide_render 渲染原稿版式页图 pNN.png → 拷贝到 test_out 供 /api/img 服务
  5. 解析讲解文本 .md，按页映射 narration（开场→1-2 页，第N页→N 页）
  6. 写 slides.json（含 img / narration，audio 留空待用户在 UI 合成）
  7. 拷贝讲解文本 .md 为配套文本文件到 decks/
"""
import os
import re
import sys
import json
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config
import pptx_parse
import slide_render

SRC_PPTX = r"D:/Users/Claw/native-shell/ppt_out/水利一张图APP介绍_内部培训.pptx"
SRC_MD = r"D:/Users/Claw/native-shell/ppt_out/水利一张图APP讲解文本.md"

DECK_NAME = os.path.basename(SRC_PPTX)
DST_PPTX = os.path.join(HERE, "sample_ppt", DECK_NAME)
DECKS_DIR = os.path.join(HERE, "decks")
DST_MD = os.path.join(DECKS_DIR, os.path.basename(SRC_MD))


def parse_narration(md_path):
    """从讲解文本 .md 解析每页讲解词：开场(1-2页) + 第N页(N页)。返回 {idx: text}。"""
    raw = open(md_path, encoding="utf-8").read()
    blocks = re.split(r"\n---\n", raw)
    nar = {}
    opening_paras = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        hm = re.search(r"^##\s*(.+)$", b, re.M)
        if not hm:
            continue
        head = hm.group(1).strip()
        body = b[hm.end():].strip()
        if head.startswith("开场"):
            opening_paras = [p.strip() for p in body.split("\n\n") if p.strip()]
        else:
            m = re.search(r"第\s*(\d+)\s*页", head)
            if m:
                nar[int(m.group(1))] = body
    if opening_paras:
        nar[1] = opening_paras[0]
        nar[2] = "\n\n".join(opening_paras[1:]) if len(opening_paras) > 1 else opening_paras[0]
    # 丢弃附录（标题是 # 附录，不是 ## 第N页，不会被收入）
    return nar


def main():
    assert os.path.exists(SRC_PPTX), "PPTX 不存在: " + SRC_PPTX
    assert os.path.exists(SRC_MD), "讲解文本不存在: " + SRC_MD

    SLIDES_FILE = os.path.join(config.DATA_DIR, "slides.json")

    # 1. 备份旧 deck
    if os.path.exists(SLIDES_FILE):
        bak = os.path.join(config.DATA_DIR, "slides_ai_demo_backup.json")
        shutil.copy(SLIDES_FILE, bak)
        print("[backup] 旧 slides.json ->", bak)

    # 2. 拷贝 PPTX 进项目
    os.makedirs(os.path.dirname(DST_PPTX), exist_ok=True)
    shutil.copy(SRC_PPTX, DST_PPTX)
    print("[copy] PPTX ->", DST_PPTX)

    # 3. 解析
    slides = pptx_parse.parse(DST_PPTX)
    print("[parse] %d 页" % len(slides))

    # 4. 渲染页图
    img_dir, paths = slide_render.render_deck(DST_PPTX)
    for p in paths:
        shutil.copy(p, os.path.join(config.DIR_TEST, os.path.basename(p)))
    print("[render] %d 页图 -> %s + 拷贝到 %s" % (len(paths), img_dir, config.DIR_TEST))

    # 5. 解析讲解词
    nar = parse_narration(SRC_MD)
    print("[narration] 映射 %d 页讲解词" % len(nar))

    # 6. 组装 SLIDES
    out = []
    for s in sorted(slides, key=lambda x: x["idx"]):
        idx = s["idx"]
        out.append({
            "idx": idx,
            "title": s.get("title", ""),
            "text": s.get("text", ""),
            "notes": s.get("notes", ""),
            "images": ["/api/img?name=" + os.path.basename(x) for x in s.get("images", [])],
            "img": ("/api/img?name=p%02d.png" % idx) if idx <= len(paths) else None,
            "audio": None,
            "narration": nar.get(idx, ""),
        })

    meta = {"source": DECK_NAME, "count": len(out), "source_path": DST_PPTX,
            "narration_md": os.path.relpath(DST_MD, HERE)}
    with open(SLIDES_FILE, "w", encoding="utf-8") as f:
        json.dump({"slides": out, "meta": meta}, f, ensure_ascii=False, indent=2)
    print("[slides.json] 写出 %d 页 -> %s" % (len(out), SLIDES_FILE))

    # 7. 配套讲解文本文件
    os.makedirs(DECKS_DIR, exist_ok=True)
    shutil.copy(SRC_MD, DST_MD)
    print("[companion] 讲解文本 ->", DST_MD)

    # 校验
    miss = [s["idx"] for s in out if not s["narration"]]
    print("[check] 缺讲解词的页:", miss if miss else "无")
    print("[check] 缺页图的页:", [s["idx"] for s in out if not s["img"]] or "无")


if __name__ == "__main__":
    main()
