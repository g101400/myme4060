# -*- coding: utf-8 -*-
"""用 ComfyUI 生成「金子」数字分身肖像（SD1.5 写实底模 majicmixRealistic_v7）。
生成 4 张候选，保存为 test_out/jinzi_*.png，供挑选后登记进象库。
"""
import json, urllib.request, urllib.error, time, os, uuid

COMFY = "http://127.0.0.1:8188"
OUTDIR = r"D:/Users/Claw/myme/test_out"
CKPT = r"SD1.5\majicmixRealistic_v7.safetensors"   # 唯一写实底模（SD1.5，Windows 反斜杠）

POS = ("photorealistic portrait, a 40-year-old East Asian woman, mature middle-aged "
       "appearance, fine subtle lines around eyes, senior IT software QA testing "
       "engineer, highly intelligent sophisticated intellectual look, gentle confident "
       "expression, wearing smart casual business blazer, thin glasses, indoor office "
       "with soft bokeh, studio soft lighting, sharp focus, highly detailed skin "
       "texture, 8k, uhd, masterpiece, best quality")
NEG = ("cartoon, anime, illustration, painting, 3d render, blurry, lowres, "
       "deformed, bad anatomy, extra fingers, mutated hands, ugly, asymmetric, "
       "heavy makeup, watermark, signature, text, nude, hat covering face, side view")

SEED = 40260929


def post(workflow):
    body = json.dumps({"prompt": workflow, "client_id": str(uuid.uuid4())}).encode()
    req = urllib.request.Request(COMFY + "/prompt", data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["prompt_id"]


def wait(prompt_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            hist = json.loads(urllib.request.urlopen(COMFY + "/history", timeout=10).read())
        except Exception:
            time.sleep(2); continue
        if prompt_id in hist:
            return hist[prompt_id]
        time.sleep(2)
    raise TimeoutError("ComfyUI 生成超时")


def download(filename, subfolder, ftype):
    qs = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": ftype})
    return urllib.request.urlopen(COMFY + "/view?" + qs, timeout=30).read()


import urllib.parse

wf = {
    "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": POS, "clip": ["3", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["3", 1]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 4}},
    "10": {"class_type": "KSampler", "inputs": {
        "model": ["3", 0], "positive": ["6", 0], "negative": ["7", 0],
        "latent_image": ["5", 0], "seed": SEED, "steps": 30, "cfg": 7.5,
        "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 2]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "jinzi40", "images": ["9", 0]}},
}

print("[gen] submitting workflow (ckpt=%s, batch=4)..." % CKPT)
pid = post(wf)
print("[gen] prompt_id=%s, waiting..." % pid)
res = wait(pid)
imgs = res["outputs"]["8"]["images"]
print("[gen] produced %d images" % len(imgs))
saved = []
for i, im in enumerate(imgs):
    data = download(im["filename"], im.get("subfolder", ""), im.get("type", "output"))
    dst = os.path.join(OUTDIR, "jinzi40_%d.png" % i)
    with open(dst, "wb") as f:
        f.write(data)
    saved.append(dst)
    print("  saved", dst, len(data), "bytes")
print("[gen] DONE:", saved)
