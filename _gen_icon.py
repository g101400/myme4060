# -*- coding: utf-8 -*-
"""用本机 ComfyUI(Z-Image-Turbo) 生成应用图标 —— 问题4。

产出 test_out/icon_gen/zi_*.png（1024x1024），后续合成 192/512/ico。
"""
import json
import os
import sys
import time
import urllib.request

API = os.environ.get("MYME_COMFYUI_API", "http://127.0.0.1:8188")
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_out", "icon_gen")
os.makedirs(OUTDIR, exist_ok=True)

# 应用主色 #0f766e(teal-700)。要"精美的和 PPT 制作有关的"：深青底 + 白色演示板/图表/播放键图形
POS = ("A polished flat-design app icon on a rounded-square tile that fills the whole frame. "
       "Background: rich deep teal gradient (#0f766e to #0b4f4a), subtle soft vignette. "
       "Centered white glyph: a minimal presentation slide board with a rising three-bar "
       "chart on it, and a small solid white triangular play button overlapping the "
       "bottom-right corner of the board. Clean geometric shapes, crisp vector edges, "
       "soft drop shadow under the glyph, premium business software icon, "
       "centered, symmetrical, uncluttered. No text.")
NEG = "text, letters, numbers, watermark, human, face, hands, photo, blurry, clutter, frame"

UNET = "Z-Image-Turbo\\z_image_turbo_bf16.safetensors"
CLIP = "qwen_3_4b.safetensors"
CLIP_TYPE = "qwen_image"
VAE = "ae.safetensors"          # Z-Image 使用 Flux AE
W, H = 1024, 1024
STEPS = 8
CFG = 1.0

def build(seed):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": CLIP_TYPE}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": W, "height": H, "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": STEPS, "cfg": CFG,
            "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0],
            "negative": ["5", 0], "latent_image": ["6", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "myme_icon"}},
    }

seeds = [int(a) for a in sys.argv[1:]] or [20261001, 20261002, 20261003, 20261004]
for seed in seeds:
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            API + "/prompt", data=json.dumps({"prompt": build(seed)}).encode("utf-8"),
            headers={"Content-Type": "application/json"}), timeout=25)
        pid = json.loads(r.read())["prompt_id"]
    except Exception as e:
        print("提交失败 seed=%s：%s" % (seed, e)); continue
    print("seed=%s prompt_id=%s" % (seed, pid))
    imgs = None
    for _ in range(90):
        time.sleep(2)
        try:
            h = json.loads(urllib.request.urlopen(API + "/history/" + pid, timeout=10).read())
        except Exception:
            continue
        if pid in h and h[pid].get("outputs"):
            for _n, o in h[pid]["outputs"].items():
                if o.get("images"):
                    imgs = o["images"]
            if imgs:
                break
    for im in (imgs or []):
        url = API + "/view?filename=" + urllib.request.quote(im["filename"]) + "&type=" + im.get("type", "output")
        dst = os.path.join(OUTDIR, "zi_%d.png" % seed)
        with urllib.request.urlopen(url, timeout=40) as f, open(dst, "wb") as g:
            g.write(f.read())
        print("  saved:", dst, os.path.getsize(dst))
