# -*- coding: utf-8 -*-
"""
对当前 slides.json 的逐页讲解词，用炎冰声纹(Qwen3-TTS, 经常驻 TTS 服务)合成 wav，挂回 slides.json。
讲解词文本保留完整（含"下面我给大家介绍"等口头语），仅 PPT 版式已去该句。
依赖：TTS 服务已启动（D:\ComfyUI_Mie_2026_V8.0 嵌式 python 跑 tts_service.py，监听 8777）。
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config
import narrate

SLIDES_FILE = os.path.join(config.DATA_DIR, "slides.json")


def main():
    d = json.load(open(SLIDES_FILE, encoding="utf-8"))
    slides = sorted(d["slides"], key=lambda x: x["idx"])
    ok_n = 0
    t_all = time.time()
    for s in slides:
        idx = s["idx"]
        text = (s.get("narration") or "").strip()
        out = os.path.join(config.DIR_NARR, "narr-%02d.wav" % idx)
        # 已合成且已挂接则跳过（可重跑补缺失页）
        if os.path.exists(out) and s.get("audio"):
            ok_n += 1
            print(f"[skip] 第{idx}页 已存在", flush=True)
            continue
        if not text:
            print(f"[skip] 第{idx}页 无讲解词", flush=True)
            continue
        t0 = time.time()
        ok = narrate.synth_one(text, "中文", out,
                               seed=config.TTS_SEED_BASE + idx, ref=config.VOICE_SAMPLE)
        if ok:
            s["audio"] = "/api/audio?name=" + os.path.basename(out)
            ok_n += 1
            print(f"[ok] 第{idx}页 -> {os.path.basename(out)} ({time.time()-t0:.1f}s)", flush=True)
        else:
            print(f"[FAIL] 第{idx}页 合成失败", flush=True)
        # 每页即时落盘，便于中断后续跑
        json.dump({"slides": d["slides"], "meta": d["meta"]},
                  open(SLIDES_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    # 合并全文音频
    wavs = []
    for s in d["slides"]:
        a = s.get("audio")
        if a:
            fp = os.path.join(config.DIR_NARR, a.split("name=")[1])
            if os.path.exists(fp):
                wavs.append(fp)
    full = narrate.concat_audio(wavs, os.path.join(config.DIR_NARR, "full_narration.wav"))
    print(f"完成 {ok_n}/{len(slides)} 页；全文音频: {full}；总耗时 {time.time()-t_all:.1f}s", flush=True)


if __name__ == "__main__":
    main()
