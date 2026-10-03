# -*- coding: utf-8 -*-
"""
myme.narrate — 讲解编排（系统 python 侧）
流程：每页 → LLM 生成口语讲稿 → 子进程调 tts_worker(嵌式python, 炎冰声纹) → 出 wav
支持中英双语；可合并为单条讲解音频。
"""
import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error
from config import (EMBEDDED_PY, DIR_NARR, TTS_SEED_BASE, FFMPEG,
                    USE_TTS_SERVICE, TTS_SERVICE_URL, svc_script)

LANG_MAP = {"中文": "Chinese", "英文": "English", "zh": "Chinese", "en": "English",
            "Chinese": "Chinese", "English": "English"}


def _synth_via_service(text, lang_code, out_path, seed, ref):
    """走常驻 TTS 服务（热模型，秒级）。成功返回 True。"""
    payload = json.dumps({
        "text": text, "lang": lang_code, "out": out_path,
        "seed": seed, "ref": ref,
    }).encode("utf-8")
    req = urllib.request.Request(TTS_SERVICE_URL + "/tts", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if not resp.get("ok") or not os.path.exists(out_path):
        print(f"[narrate] 服务合成失败: {resp}", flush=True)
        return False
    return True


def _synth_via_subprocess(text, lang_code, out_path, seed, ref):
    """回退：每次冷启动嵌式 python 子进程跑 tts_worker。"""
    worker = svc_script("tts_worker.py")
    cmd = [EMBEDDED_PY, worker,
           "--text", text, "--lang", lang_code, "--out", out_path, "--seed", str(seed)]
    if ref:
        cmd += ["--ref", ref]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       timeout=300, cwd=os.path.dirname(worker))
    if r.returncode != 0 or not os.path.exists(out_path):
        print(f"[narrate] TTS 失败: {out_path}\n{r.stderr[-500:]}", flush=True)
        return False
    return True


def synth_one(text, lang, out_path, seed, ref=None):
    """调 Qwen3-TTS 克隆声。优先常驻服务（热），失败回退冷启动子进程。返回 True/False。"""
    lang_code = LANG_MAP.get(lang, "Chinese")
    if USE_TTS_SERVICE:
        try:
            return _synth_via_service(text, lang_code, out_path, seed, ref)
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError, TimeoutError) as e:
            print(f"[narrate] TTS 服务不可用，回退子进程: {e}", flush=True)
    return _synth_via_subprocess(text, lang_code, out_path, seed, ref)


def narrate_slides(slides, lang="中文", words=200, out_dir=DIR_NARR, ref=None, model=None):
    from qa_brain import generate_narration
    os.makedirs(out_dir, exist_ok=True)
    total = len(slides)
    for sl in slides:
        sl["_total"] = total
    results = []
    for i, sl in enumerate(slides, 1):
        print(f"[narrate] 第{sl['idx']}页 生成讲稿...", flush=True)
        text = generate_narration(sl, lang=lang, words=words, model=model or "qwen3.5:9b")
        out = os.path.join(out_dir, f"narr-{sl['idx']:02d}.wav")
        ok = synth_one(text, lang, out, seed=TTS_SEED_BASE + i, ref=ref)
        results.append({"idx": sl["idx"], "title": sl.get("title", ""),
                        "narration": text, "audio": out if ok else None})
    return results


def concat_audio(wav_list, out_path, fade=0.3):
    """把多条 wav 合并为一条（静音间隔 + 可选淡入淡出）。"""
    valid = [w for w in wav_list if w and os.path.exists(w)]
    if not valid:
        return None
    if len(valid) == 1:
        # 直接复制
        subprocess.run([FFMPEG, "-y", "-i", valid[0], "-ar", "24000", "-ac", "1", out_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path
    # 用 concat 协议
    list_file = out_path + ".list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for w in valid:
            f.write(f"file '{os.path.abspath(w)}'\n")
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                    "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(list_file):
        os.remove(list_file)
    return out_path if os.path.exists(out_path) else None


def save_script(results, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return out_path


if __name__ == "__main__":
    import pptx_parse, knowledge_base, qa_brain
    # 端到端小测（需先有 pptx 路径作为 argv[1]）
    p = sys.argv[1] if len(sys.argv) > 1 else None
    if not p:
        print("用法: python narrate.py <file.pptx> [lang]")
        sys.exit(0)
    lang = sys.argv[2] if len(sys.argv) > 2 else "中文"
    slides = pptx_parse.parse(p)
    res = narrate_slides(slides, lang=lang)
    save_script(res, os.path.join(DIR_NARR, "script.json"))
    wavs = [r["audio"] for r in res]
    full = concat_audio(wavs, os.path.join(DIR_NARR, "full_narration.wav"))
    print("讲解音频:", full)
