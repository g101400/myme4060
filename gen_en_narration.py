# -*- coding: utf-8 -*-
"""给「当前演示版本」补录英文讲稿 + 英文配音（只做英文，不碰已有中文音频）。

用途：导出播放包时四片段（炎冰/金子 × 中/英）里的两段英文要真有配音，
否则播放端切中文正常、切英文没声音，用户只会以为"切英文没反应"。

跑法：cd 到本目录后  python gen_en_narration.py
可先用 MYME_DATA=... 指定数据目录（默认按 config 的规则取）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config  # noqa: E402
config.init_projects()

import app  # noqa: E402


def log(*a):
    print("[en] " + " ".join(str(x) for x in a), flush=True)


def _persist(vid, base):
    """把 base 里翻好的英文讲稿回写进当前版本，并让 audio_en 只指向真实存在的文件。

    audio_en 指向不存在的文件，播放端点英文就 404 —— 宁可留空、由播放器自己提示"没英文配音"。
    """
    for vs in (app.VERSIONS.get(vid) or {}).get("slides", []):
        for sl0 in base:
            if sl0.get("idx") == vs.get("idx"):
                vs["narration_en"] = sl0.get("narration_en", "")
                fn = "narr_%s_%02d_en.wav" % (vid, sl0.get("idx"))
                has = bool(sl0.get("narration_en")) and os.path.isfile(os.path.join(config.DIR_NARR, fn))
                vs["audio_en"] = ("/api/audio?name=" + fn) if has else None
    app._save_slides()


def _probe_tts():
    """先看 TTS 常驻服务在不在（一次 HTTP 探活，3 秒封顶）。

    ⚠ 不要拿"真合成一次"当探测：服务没起来时 narrate 会回退到子进程，
    那是要冷启动 ComfyUI 嵌式 python 的，一次好几分钟，探个活能卡上一节课。
    探活不通就只落英文讲稿，等服务起来了重跑本脚本即可（讲稿会跳过、只补配音）。
    """
    # ⚠ 探活必须打 /ready：tts_service.py 只实现 /ready 与 /tts，打根路径 "/" 会拿到 404，
    # urllib 抛 HTTPError，于是被误判成「服务没起来」→ 整轮英文配音全被跳过。
    host = config.TTS_SERVICE_URL.split("//")[-1].split(":")[0] if "//" in config.TTS_SERVICE_URL else "127.0.0.1"
    url = "http://%s:%d/ready" % (host, config.TTS_SERVICE_PORT)
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=3) as r:
            info = r.read(200).decode("utf-8", "replace")
    except Exception as e:
        log("TTS 常驻服务不在（%s）——只落英文讲稿，英文配音稍后补；"
            "跑法：先让 start_myme.bat 拉起 TTS，再跑本脚本" % type(e).__name__)
        return False
    # /ready 里带模型名，能顺带确认不是「端口活着但模型没加载」
    log("TTS 常驻服务在:", info[:120])
    return True


def main():
    app._load_slides()
    vid = app.CURRENT_VERSION
    base = (app.VERSIONS.get(vid) or {}).get("slides") or []
    if not base:
        log("当前版本没有讲稿，先去「④ 分身语音讲解」跑一遍中文")
        return 1
    log("当前版本:", vid, "页数:", len(base), "| NARR_BILINGUAL =", app.NARR_BILINGUAL)
    log("用法：本脚本先落英文讲稿，再逐页合成英文配音；"
        "TTS 常驻服务（127.0.0.1:8777）没起来时会自动跳过配音、只留讲稿，不卡死。")
    t0 = time.time()
    text_n = audio_n = fail_n = 0
    # TTS 可用性探测（只做一次）：常驻服务没拉起时子进程回退也会连不上，
    # 每页都试一次会白白卡十几秒再失败，30 页就是七八分钟。
    tts_ok = None
    for i, sl in enumerate(base, 1):
        fn_audio = "narr_%s_%02d_en.wav" % (vid, sl["idx"])
        p_audio = os.path.join(config.DIR_NARR, fn_audio)
        # 断点续跑的判据只有「讲稿有」是不够的：真正要跳过的是「讲稿和配音都已经在」。
        # 旧写法把两者混为一谈 —— 那 9 页只有讲稿没有配音，每轮都被整页跳过，
        # 英文音频永远补不上，导出清单只能一直标「缺英文配音」。
        en = (sl.get("narration_en") or "").strip()
        if en and os.path.isfile(p_audio):
            log("第 %d/%d 页 讲稿与配音都在，跳过" % (i, len(base)))
            continue
        if not en:
            try:
                en = app._translate_text(sl.get("narration", "") or "", "English")
            except Exception as e:
                log("第 %d 页英文讲稿失败: %s" % (i, e))
                en = ""
            sl["narration_en"] = en
            log("第 %d/%d 页 英文讲稿 %d 字" % (i, len(base), len(en)))
        if not en:
            continue
        text_n += 1
        # 边翻边存：30 页一翻就要七八分钟，攒到最后才写盘，中途一挂全部作废
        _persist(vid, base)
        out_en = p_audio
        try:
            if tts_ok is None:
                tts_ok = _probe_tts()
            if not tts_ok:
                continue
            ok_en = app.narrate.synth_one(en, "English", out_en,
                                          seed=app.TTS_SEED_BASE + 100 + i,
                                          ref=app.resolve_voice())
            if not ok_en:
                tts_ok, fail_n = False, fail_n + 1
        except Exception as e:
            log("第 %d 页英文配音失败: %s" % (i, e))
            ok_en = False
            tts_ok = False
        if tts_ok:
            audio_n += 1
    try:
        _persist(vid, base)
        log("已回写 slides.json")
    except Exception as e:
        log("回写失败: %s" % e)
        return 2
    log("完成：英文讲稿 %d 页 / 英文配音 %d 页 / 失败 %d 页，用时 %.1fs"
        % (text_n, audio_n, fail_n, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
