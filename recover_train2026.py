# -*- coding: utf-8 -*-
"""
恢复脚本：从既有产物重建 myme 的 train2026（培训版 2026-09-26，30 页）版本，
不重跑 TTS（narr_train2026_*.wav 已存在），仅复用：
  - pptx 解析（标题/正文/备注/内嵌图）
  - test_out/train2026_pNN.png（整页图，已渲染）
  - test_out/train2026_*（内嵌图，已拷贝）
  - narrations/narr_train2026_NN.wav（音频，已合成）
  - 讲稿 md（逐页 narration）
写入 slides.json 的 VERSIONS，并登记到 meta.versions（VERSION_ORDER），
同时完整保留 default 版本。
"""
import os
import re
import sys
import json
import time
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config
import pptx_parse

SRC_PPTX = r"D:/Users/ppt/水利一张图APP介绍_培训PPT_20260926.pptx"
SRC_MD = r"D:/Users/ppt/水利一张图APP培训_讲解文本_数字分身用.md"
IMG_PREFIX = "train2026_"
VER_ID = "train2026"
VER_NAME = "培训版 2026-09-26"

SLIDES_FILE = os.path.join(config.DATA_DIR, "slides.json")
DIR_TEST = config.DIR_TEST
DIR_NARR = config.DIR_NARR


def parse_narration(md_path):
    raw = open(md_path, encoding="utf-8").read()
    parts = re.split(r"(?m)^##\s+", raw)
    nar = {}
    for p in parts:
        p = p.strip()
        if not p:
            continue
        lines = p.split("\n", 1)
        head = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        m = re.search(r"第\s*(\d+)\s*页", head)
        if m:
            nar[int(m.group(1))] = body
    return nar


def main():
    assert os.path.exists(SRC_PPTX), "PPTX 不存在"
    assert os.path.exists(SRC_MD), "讲解文本不存在"

    slides = pptx_parse.parse(SRC_PPTX)
    slides.sort(key=lambda x: x["idx"])
    print("[parse] %d 页" % len(slides))

    nar = parse_narration(SRC_MD)
    print("[narration] %d 页讲解词" % len(nar))

    out = []
    for s in slides:
        idx = s["idx"]
        text = nar.get(idx, "")
        # 整页图
        pg = "%sp%02d.png" % (IMG_PREFIX, idx)
        img = ("/api/img?name=" + pg) if os.path.exists(os.path.join(DIR_TEST, pg)) else None
        # 内嵌图（复用已拷贝到 test_out 的 train2026_* 前缀文件）
        imgs = []
        for x in s.get("images", []):
            if not x or not os.path.exists(x):
                continue
            dn = IMG_PREFIX + os.path.basename(x)
            dst = os.path.join(DIR_TEST, dn)
            if not os.path.exists(dst):
                shutil.copy(x, dst)
            imgs.append("/api/img?name=" + dn)
        # 音频（已合成）
        aw = "narr_%s_%02d.wav" % (VER_ID, idx)
        audio = ("/api/audio?name=" + aw) if os.path.exists(os.path.join(DIR_NARR, aw)) else None
        out.append({
            "idx": idx,
            "title": s.get("title", ""),
            "text": s.get("text", ""),
            "notes": s.get("notes", ""),
            "images": imgs,
            "img": img,
            "audio": audio,
            "narration": text,
        })
        print("[slide %02d] img=%s audio=%s" % (idx, "OK" if img else "无", "OK" if audio else "无"))

    # 全篇音频
    audio_full = None
    try:
        import narrate
        wavs = [os.path.join(DIR_NARR, "narr_%s_%02d.wav" % (VER_ID, s["idx"]))
                for s in out if s["audio"]]
        if wavs:
            full = narrate.concat_audio(wavs, os.path.join(DIR_NARR, "full_%s.wav" % VER_ID))
            if full and os.path.exists(full):
                audio_full = "/api/audio?name=" + os.path.basename(full)
    except Exception as e:
        print("[warn] 全篇音频合并失败（不影响逐页）: %s" % e)

    # 写入 slides.json：保留 default，新增/覆盖 train2026，并登记到 meta.versions
    with open(SLIDES_FILE, "r", encoding="utf-8") as f:
        d = json.load(f)
    versions = d.get("versions") or {}
    meta = d.get("meta", {})
    order = [v.get("id") for v in (meta.get("versions") or [])] or list(versions.keys())
    versions[VER_ID] = {
        "name": VER_NAME,
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "slides": out,
        "audio_full": audio_full,
    }
    if VER_ID not in order:
        order.append(VER_ID)
    meta["versions"] = [{"id": k, "name": versions[k]["name"],
                         "created": versions[k].get("created", "")} for k in order]
    d["versions"] = versions
    d["meta"] = meta
    with open(SLIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    miss = [s["idx"] for s in out if not s["audio"]]
    missn = [s["idx"] for s in out if not s["narration"]]
    missi = [s["idx"] for s in out if not s["img"]]
    print("[slides.json] 版本 %s 重建完成（%d 页）；当前版本=%s"
          % (VER_ID, len(out), meta.get("currentVersion")))
    print("[check] 缺音频页:", miss if miss else "无")
    print("[check] 缺讲解词页:", missn if missn else "无")
    print("[check] 缺整页图页:", missi if missi else "无")
    print("[versions] 现有版本顺序:", order)


if __name__ == "__main__":
    main()
