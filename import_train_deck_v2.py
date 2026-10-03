# -*- coding: utf-8 -*-
"""
把《水利一张图APP介绍_培训PPT_20260926》(30页) + 配套讲解文本 导入 myme，
作为独立版本「培训版 2026-09-26」(id=train2026)，与现有 default(20页) 共存。

流程：
  1. 解析 pptx -> 30 页脚手架(idx/title/text/notes/images)
  2. 渲染原稿页图 -> 重命名为 train2026_pNN.png 存入 test_out（避免与 default 的 pNN.png 冲突）
  3. 解析讲解文本 .md -> 逐页 narration（## 第 N 页）
  4. 用 TTS 服务(炎冰声纹)逐页合成音频 narr_train2026_NN.wav
  5. 组装完整 slides（含 img/images/narration/audio）
  6. 写入 slides.json 的 VERSIONS（保留 default 为当前版本，不破坏现有演示态）
"""
import os
import re
import sys
import json
import shutil
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import config
import pptx_parse
import slide_render
import narrate
import subprocess

SRC_PPTX = r"D:/Users/ppt/水利一张图APP介绍_培训PPT_20260926.pptx"
# 讲解文本可选：命令行 --narr 指定；不指定则逐页按幻灯片内容自动生成
SRC_MD = None
IMG_PREFIX = "train2026_"
VER_ID = "train2026"
VER_NAME = "培训版 2026-09-26"

SLIDES_FILE = os.path.join(config.DATA_DIR, "slides.json")
DIR_TEST = config.DIR_TEST
DIR_NARR = config.DIR_NARR


def parse_narration(md_path):
    """解析讲解文本 -> {页码: 讲解词}。讲解文本可选，缺失页由调用方自动生成。
    统一走 deck_narration：兼容「## 第 N 页」「第 N 页」「--- 分隔」「纯分段」四种写法。"""
    import deck_narration
    mapping, stats = deck_narration.parse_narration_text(
        deck_narration.read_narration_file(md_path))
    print("  [narration] 解析方式=%s，命中 %d 页，丢弃 %d 块"
          % (stats["mode"], len(mapping), stats.get("dropped", 0)))
    return mapping


def auto_narration(slide, words=160, lang="中文"):
    """没有讲解文本时，按幻灯片内容自动生成口播稿（本地 Ollama）。"""
    from qa_brain import generate_narration
    return generate_narration(slide, lang=lang, words=words, model="qwen3.5:9b")


def synth_subprocess(text, out_wav, seed, ref, lang="Chinese", timeout=150, tries=2):
    """用 tts_worker.py 冷启动子进程合成（每次干净 CUDA 上下文，规避常驻服务偶发卡死）。
    带超时与重试；超时则杀掉子进程重试。成功返回 True。"""
    cmd = [config.EMBEDDED_PY, os.path.join(HERE, "tts_worker.py"),
           "--text", text, "--lang", lang, "--out", out_wav,
           "--seed", str(seed), "--ref", ref]
    for t in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", timeout=timeout)
            if r.returncode == 0 and os.path.exists(out_wav):
                return True
            print("  [tts try %d] rc=%s err=%s" % (t + 1, r.returncode, r.stderr[-200:].replace("\n", " ")))
        except subprocess.TimeoutExpired:
            print("  [tts try %d] 超时(%ss)，杀掉重试" % (t + 1, timeout))
    return False


def _parse_args():
    import argparse
    ap = argparse.ArgumentParser(
        description="导入 PPT 生成数字分身讲解版本（PPT 必选，讲解文本可选）")
    ap.add_argument("--pptx", default=SRC_PPTX, help="演示文稿 .pptx / .ppt（必选）")
    ap.add_argument("--narr", default=SRC_MD,
                    help="讲解文本 .md/.txt（可选；不给则逐页按幻灯片自动生成）")
    ap.add_argument("--ver", default=VER_ID, help="版本 id，默认 train2026")
    ap.add_argument("--name", default=VER_NAME, help="版本显示名")
    return ap.parse_args()


def main():
    global SRC_PPTX, SRC_MD, VER_ID, VER_NAME, IMG_PREFIX
    args = _parse_args()
    SRC_PPTX, SRC_MD = args.pptx, args.narr
    VER_ID, VER_NAME = args.ver, args.name
    IMG_PREFIX = VER_ID + "_"
    assert os.path.exists(SRC_PPTX), "PPTX 不存在: " + SRC_PPTX
    os.makedirs(DIR_TEST, exist_ok=True)
    os.makedirs(DIR_NARR, exist_ok=True)

    # 1) 解析 pptx
    slides = pptx_parse.parse(SRC_PPTX)
    slides.sort(key=lambda x: x["idx"])
    print("[parse] %d 页" % len(slides))

    # 2) 渲染页图 -> train2026_pNN.png
    img_dir, paths = slide_render.render_deck(SRC_PPTX)
    name_map = {}
    for p in paths:
        m = re.search(r"p(\d+)\.png", os.path.basename(p))
        if not m:
            continue
        i = int(m.group(1))
        dst = os.path.join(DIR_TEST, IMG_PREFIX + "p%02d.png" % i)
        shutil.copy(p, dst)
        name_map[i] = IMG_PREFIX + "p%02d.png" % i
    print("[render] %d 页图 -> %s" % (len(paths), DIR_TEST))

    # 3) 讲解词：给了讲解文本就按文本，没给（或某页缺失）就按幻灯片自动生成
    nar, n_used, n_gen = {}, 0, 0
    if SRC_MD and os.path.exists(SRC_MD):
        nar = parse_narration(SRC_MD)
        print("[narration] 讲解文本：%d 页" % len(nar))
    else:
        print("[narration] 未提供讲解文本 -> 逐页按幻灯片自动生成")

    # 4) 组装完整 slides + 逐页合成音频
    out = []
    total = len(slides)
    for s in slides:
        idx = s["idx"]
        text = nar.get(idx, "")
        if text:
            n_used += 1
        else:
            text = auto_narration(s)
            n_gen += 1
        # 内嵌图：拷贝到 test_out 并加前缀避免与 default 冲突
        imgs = []
        for x in s.get("images", []):
            if os.path.exists(x):
                dn = IMG_PREFIX + os.path.basename(x)
                shutil.copy(x, os.path.join(DIR_TEST, dn))
                imgs.append("/api/img?name=" + dn)
        audio_url = None
        if text:
            out_wav = os.path.join(DIR_NARR, "narr_%s_%02d.wav" % (VER_ID, idx))
            ok = synth_subprocess(text, out_wav, config.TTS_SEED_BASE + idx, config.VOICE_SAMPLE)
            if ok:
                audio_url = "/api/audio?name=" + os.path.basename(out_wav)
        out.append({
            "idx": idx,
            "title": s.get("title", ""),
            "text": s.get("text", ""),
            "notes": s.get("notes", ""),
            "images": imgs,
            "img": ("/api/img?name=" + name_map[idx]) if idx in name_map else None,
            "audio": audio_url,
            "narration": text,
        })
        print("[slide %02d] %s audio=%s"
              % (idx, "讲稿文本" if idx in nar else "自动生成",
                 "OK" if audio_url else "失败"))

    # 5) 合并全篇音频
    wavs = [os.path.join(DIR_NARR, "narr_%s_%02d.wav" % (VER_ID, s["idx"]))
            for s in out if s["audio"]]
    full = narrate.concat_audio(wavs, os.path.join(DIR_NARR, "full_%s.wav" % VER_ID)) if wavs else None
    audio_full = ("/api/audio?name=" + os.path.basename(full)) if full else None

    # 6) 写入 slides.json：新增版本，保留 default 为当前
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
    print("[slides.json] 新增版本 %s（%d 页）；当前版本保持 %s"
          % (VER_ID, len(out), meta.get("currentVersion")))
    print("[check] 缺音频页:", miss if miss else "无")
    print("[check] 缺讲解词页:", [s["idx"] for s in out if not s["narration"]] or "无")
    print("[check] 讲稿来源: 文本 %d 页 / 自动生成 %d 页" % (n_used, n_gen))


if __name__ == "__main__":
    main()
