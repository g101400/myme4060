# -*- coding: utf-8 -*-
"""帧差检测（区域感知 + 静止对照）：确认数字分身视频存在微动，排除编码噪声。
用法: python _framediff.py <video> [fps]
- 全局 MAD：整帧灰度平均绝对差（背景会稀释局部面部运动）
- 面部 MAD：仅取画面中央人脸区域（裁剪 宽30%-70% / 高20%-78%）
- 对照：用首帧反复复制成"静止视频"，其 MAD≈编码噪声底，作为基线
判定：若 面部MAD 明显 > 静止对照，且存在周期性尖峰（眨眼/转头），则确认微动 ✅
"""
import sys, os, subprocess, shutil, tempfile
from PIL import Image

FFMPEG = r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"

def extract(video, fps=8, outdir=None):
    if outdir is None:
        outdir = tempfile.mkdtemp(prefix="fd_")
    pat = os.path.join(outdir, "f%05d.png")
    subprocess.run([FFMPEG, "-y", "-i", video, "-vf", f"fps={fps}", pat],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return sorted(os.path.join(outdir, f) for f in os.listdir(outdir) if f.endswith(".png")), outdir

def mad(a, b):
    pa = a.convert("L").resize((192, 256)).load()
    pb = b.convert("L").resize((192, 256)).load()
    s = 0
    for y in range(256):
        for x in range(192):
            s += abs(pa[x, y] - pb[x, y])
    return s / (192 * 256)

def mad_face(a, b):
    # 裁剪人脸区域（中央），放大以突出局部运动
    def crop(im):
        w, h = im.size
        return im.crop((int(w*0.30), int(h*0.20), int(w*0.70), int(h*0.78))).resize((128, 160))
    pa = crop(a).convert("L").load(); pb = crop(b).convert("L").load()
    s = 0
    for y in range(160):
        for x in range(128):
            s += abs(pa[x, y] - pb[x, y])
    return s / (128 * 160)

def stats(vals):
    if not vals: return (0, 0, 0)
    return max(vals), min(vals), sum(vals)/len(vals)

def main():
    video = sys.argv[1]
    fps = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print(f"[*] 视频: {video}")
    fs, d = extract(video, fps)
    print(f"[*] 提取 {len(fs)} 帧 @ {fps}fps")

    gm, fm = [], []
    for i in range(1, len(fs)):
        a, b = Image.open(fs[i-1]), Image.open(fs[i])
        gm.append(mad(a, b)); fm.append(mad_face(a, b))

    # 静止对照：首帧复制到全部
    ctrl_dir = tempfile.mkdtemp(prefix="fdctrl_")
    f0 = Image.open(fs[0])
    for i in range(len(fs)):
        f0.save(os.path.join(ctrl_dir, f"f{i:05d}.png"))
    cf = sorted(os.path.join(ctrl_dir, f) for f in os.listdir(ctrl_dir) if f.endswith(".png"))
    cfm = [mad_face(Image.open(cf[i-1]), Image.open(cf[i])) for i in range(1, len(cf))]
    ctrl_max, ctrl_mean = stats(cfm)[0], stats(cfm)[1] or 0
    ctrl_mean = stats(cfm)[2]

    gmx, gmn, gmean = stats(gm)
    fmx, fmn, fmean = stats(fm)
    peaks = sum(1 for v in fm if v > ctrl_mean + 1.5)  # 超过对照基线1.5以上的帧对=可见运动
    print(f"[*] 全局 MAD  max={gmx:.2f} min={gmn:.2f} mean={gmean:.2f}")
    print(f"[*] 面部 MAD  max={fmx:.2f} min={fmn:.2f} mean={fmean:.2f}")
    print(f"[*] 静止对照 面部MAD max={ctrl_max:.3f} mean={ctrl_mean:.3f}  (编码噪声底)")
    print(f"[*] 面部运动尖峰(超基线): {peaks}/{len(fm)} 帧对")

    if fmean > ctrl_mean * 2 and peaks >= len(fm) * 0.25:
        print("[OK] 面部运动显著超过静止噪声底且存在持续微动 —— 数字分身细腻真实 ✅")
    elif fmean > ctrl_mean * 1.5:
        print("[~] 检测到轻微面部变化，微动偏弱，建议提高 _motion_plan 幅度")
    else:
        print("[X] 面部近乎静止，与噪声底无异 —— 微动缺失，需排查 ❌")
    shutil.rmtree(d, ignore_errors=True); shutil.rmtree(ctrl_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
