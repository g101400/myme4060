# -*- coding: utf-8 -*-
"""生成内置分身演示四片段：炎冰/金子 × 中文/英文。
用法：
  python gen_demo_clips.py          # 生成全部 4 条
  python gen_demo_clips.py 0        # 仅生成第 0 条（yanbing·中文），用于冒烟测试
肖像图取自 android-player/assets/portraits（保证存在）；
音频用对应分身声纹样本克隆（voice/<id>-sample-01.wav）；
视频经 Sonic（ComfyUI 扩散式）驱动，失败回退 LivePortrait。
产出写入 demo_avatar/<id>_<cn|en>.mp4，myme.spec 会整体打进 bundle。
"""
import os, sys, time, json

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import config
import narrate
import avatar_sonic

DEMO = os.path.join(ROOT, "demo_avatar")
ASSETS = os.path.join(ROOT, "android-player", "assets", "portraits")
VOICE = os.path.join(ROOT, "voice")
os.makedirs(DEMO, exist_ok=True)

JOBS = [
    ("yanbing", "炎冰", "selfie.jpg", "yanbing-sample-01.wav", "中文",
     "大家好，我是炎冰。欢迎体验这套本地运行的数字分身演示系统，下面由我为您讲解。"),
    ("yanbing", "炎冰", "selfie.jpg", "yanbing-sample-01.wav", "英文",
     "Hello everyone, I am Yanbing. Welcome to this locally-run digital avatar demonstration system."),
    ("jinzi", "金子", "jinzi__meet.png", "jinzi-sample-01.wav", "中文",
     "大家好，我是金子。这套分身系统全部在本机运行，数据不会上传到任何云端。"),
    ("jinzi", "金子", "jinzi__meet.png", "jinzi-sample-01.wav", "英文",
     "Hello everyone, I am Jinzi. This avatar system runs entirely on your local machine, and no data is uploaded to the cloud."),
]


def gen(pid, pname, img_rel, voice_file, lang, text):
    tag = "en" if lang == "英文" else "cn"
    img = os.path.join(ASSETS, img_rel)
    ref = os.path.abspath(os.path.join(VOICE, voice_file))
    wav = os.path.abspath(os.path.join(DEMO, "%s_%s.wav" % (pid, tag)))
    mp4 = os.path.abspath(os.path.join(DEMO, "%s_%s.mp4" % (pid, tag)))
    print("[gen] === %s / %s ===" % (pid, lang), flush=True)
    if not os.path.exists(img):
        print("[gen] 肖像图缺失: %s" % img, flush=True); return False
    # 1) TTS（必须用绝对 out 路径，否则常驻服务写到自己 cwd 导致本地 0 字节）
    seed = 1000 + (abs(hash(text)) % 1000)
    ok = narrate.synth_one(text, lang, wav, seed, ref=ref)
    if not ok or not os.path.exists(wav) or os.path.getsize(wav) == 0:
        print("[gen] TTS 失败 %s/%s" % (pid, lang), flush=True); return False
    print("[gen] TTS ok -> %s (%d bytes)" % (wav, os.path.getsize(wav)), flush=True)
    # 2) Sonic 扩散式驱动（ComfyUI）
    sr = avatar_sonic.drive_avatar_sonic(img, wav, mp4, url=config.SONIC_URL, timeout=1800)
    if sr.get("ok") and os.path.exists(mp4) and os.path.getsize(mp4) > 0:
        print("[gen] Sonic ok -> %s (%d bytes)" % (mp4, os.path.getsize(mp4)), flush=True); return True
    print("[gen] Sonic 失败: %s" % json.dumps(sr, ensure_ascii=False), flush=True)
    # 3) 回退 LivePortrait（需嵌式 python）
    try:
        import avatar
        dr = avatar.drive_avatar(img, wav, mp4, resolution=config.AVATAR_RES, prepare=False, motion=1.0)
        if dr.get("ok") and os.path.exists(mp4) and os.path.getsize(mp4) > 0:
            print("[gen] LivePortrait ok -> %s" % mp4, flush=True); return True
        print("[gen] LivePortrait 失败: %s" % json.dumps(dr, ensure_ascii=False), flush=True)
    except Exception as e:
        print("[gen] LivePortrait 异常: %s" % e, flush=True)
    return False


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    jobs = [JOBS[int(arg)]] if arg is not None else JOBS
    results = {}
    t0 = time.time()
    for pid, pname, img_rel, vf, lang, text in jobs:
        key = "%s_%s" % (pid, lang)
        try:
            results[key] = gen(pid, pname, img_rel, vf, lang, text)
        except Exception as e:
            results[key] = "EXC:%s" % e
            print("[gen] 异常 %s: %s" % (key, e), flush=True)
    print("=== 结果（耗时 %.1fs）===" % (time.time() - t0), flush=True)
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
