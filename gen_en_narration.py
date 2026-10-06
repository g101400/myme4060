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


def _probe_tts(vid):
    """探一次英文配音能不能出。出不来就别在每一页上重复烧时间。

    典型情形：TTS 常驻服务（127.0.0.1:8777）没拉起，回退子进程也连不上，
    此时仍先把英文讲稿落盘，等服务起来了再补配音。
    """
    probe = os.path.join(config.DIR_NARR, "narr_%s_probe.wav" % vid)
    try:
        ok = app.narrate.synth_one("probe", "English", probe,
                                   seed=1, ref=app.resolve_voice())
    except Exception as e:
        log("TTS 探测失败，跳过英文配音（%s）" % e)
        return False
    if not ok:
        log("TTS 探测失败（服务未响应），跳过英文配音；英文讲稿照常落盘")
        return False
    try:
        if os.path.exists(probe):
            os.remove(probe)
    except Exception:
        pass
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
        # 断点续跑：已经翻过的页不再重复烧大模型（上次跑挂了也不白干）
        if (sl.get("narration_en") or "").strip():
            log("第 %d/%d 页 已有英文讲稿，跳过" % (i, len(base)))
            continue
        en = (sl.get("narration_en") or "").strip()
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
        out_en = os.path.join(config.DIR_NARR, "narr_%s_%02d_en.wav" % (vid, sl["idx"]))
        try:
            if tts_ok is None:
                tts_ok = _probe_tts(vid)
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
