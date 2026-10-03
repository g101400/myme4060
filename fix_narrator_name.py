# -*- coding: utf-8 -*-
"""
把讲解里的自我介绍统一修正为「我是科技推广中心耿晓光」，
同步修正 slides.json / 讲解文本源文件，并重新合成受影响页的语音 + 重建全篇音频。

历史：v1 曾误改为「我是挺广中心耿晓光」，v2 纠正为「我是科技推广中心耿晓光」。

用法：
    python fix_narrator_name.py            # 改文案 + 重合成受影响页
    python fix_narrator_name.py --text-only  # 只改文案，不动音频（TTS 不可用时用）
"""
import os
import re
import sys
import json
import shutil
import datetime

import config
import narrate

NEW_ZH = "我是科技推广中心耿晓光"      # 正确署名（单位=科技推广中心，姓名=耿晓光）
# 需要被纠正的历史写法（按出现先后依次替换）
OLD_ZH = "我是挺广中心耿晓光"          # v1 误改后的写法
OLD_ZH0 = "我是科技推广中心的小七"     # 最初写法
OLD_ZH2 = "我是科技推广中心的讲解员"   # train2026 开场（同一处自我介绍）
OLD_EN = "I'm Xiao Qi from the Technology Promotion Center"
OLD_EN2 = "I'm Geng Xiaoguang from the Tingguang Center"
NEW_EN = "I'm Geng Xiaoguang from the Technology Promotion Center"

SLIDES_JSON = os.path.join(config.WORKSPACE, "slides.json")
DIR_NARR = config.DIR_NARR
# 讲解文本源文件（导入 PPT 用的讲稿），同样要改，否则下次重新导入又会变回去
DECK_TEXT = os.path.join(config.WORKSPACE, "decks", "水利一张图APP讲解文本.md")

# 受影响页：版本 -> (页 idx, 音频文件名, 合成 seed)
TARGETS = {
    "default":   [("1", "narr-01.wav", config.TTS_SEED_BASE + 1)],
    "train2026": [("1", "narr_train2026_01.wav", config.TTS_SEED_BASE + 1)],
}


def backup():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = SLIDES_JSON + ".bak_" + ts
    shutil.copy2(SLIDES_JSON, dst)
    print("[backup] slides.json -> %s" % os.path.basename(dst))


def fix_text():
    d = json.load(open(SLIDES_JSON, encoding="utf-8"))
    changed = []
    # 注意：顶层 slides 是当前版本的"展开副本"，与 versions[*].slides 是两份数据，都要改，
    # 否则前端 /api/slides 拿到的仍是旧文案。
    groups = list(d.get("versions", {}).items()) + [("__top__", {"slides": d.get("slides", [])})]
    for vid, ver in groups:
        for sl in ver.get("slides", []):
            for field in ("narration", "narration_en"):
                t = sl.get(field) or ""
                if not t:
                    continue
                nt = t
                if field == "narration":
                    nt = nt.replace(OLD_ZH, NEW_ZH).replace(OLD_ZH0, NEW_ZH)
                    if vid == "train2026":
                        nt = nt.replace(OLD_ZH2, NEW_ZH)
                else:
                    nt = nt.replace(OLD_EN, NEW_EN).replace(OLD_EN2, NEW_EN)
                if nt != t:
                    sl[field] = nt
                    changed.append((vid, sl.get("idx"), field))
    json.dump(d, open(SLIDES_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[text] 修改 %d 处：%s" % (len(changed), changed or "无"))
    return d


def fix_deck_text():
    """改讲解文本源文件，避免下次重新导入 PPT 时把旧署名带回来。"""
    if not os.path.exists(DECK_TEXT):
        print("[deck] 源文件缺失，跳过：%s" % DECK_TEXT); return 0
    t = open(DECK_TEXT, encoding="utf-8").read()
    nt = t.replace(OLD_ZH, NEW_ZH).replace(OLD_ZH0, NEW_ZH).replace(OLD_ZH2, NEW_ZH)
    n = sum(t.count(x) for x in (OLD_ZH, OLD_ZH0, OLD_ZH2))
    if nt != t:
        shutil.copy2(DECK_TEXT, DECK_TEXT + ".bak_" +
                     datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        open(DECK_TEXT, "w", encoding="utf-8").write(nt)
    print("[deck] %s 修改 %d 处" % (os.path.basename(DECK_TEXT), n))
    return n


def fix_kb():
    """知识库里若残留旧署名，就地替换文本（仅文本，向量不变）。"""
    p = os.path.join(config.WORKSPACE, "kb_data", "myme_kb.json")
    if not os.path.exists(p):
        print("[kb] 知识库缺失，跳过"); return 0
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print("[kb] 读取失败：%s" % e); return 0
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and (OLD_ZH in v or OLD_ZH0 in v or OLD_ZH2 in v):
                    o[k] = v.replace(OLD_ZH, NEW_ZH).replace(OLD_ZH0, NEW_ZH).replace(OLD_ZH2, NEW_ZH)
                    n += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    if n:
        json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print("[kb] 知识库修改 %d 处" % n)
    return n


def wav_list(ver, d):
    """按页序取该版本所有音频的本地绝对路径（缺文件则跳过）。"""
    out = []
    for sl in ver.get("slides", []):
        a = sl.get("audio") or ""
        name = a.split("name=")[-1] if "name=" in a else os.path.basename(a.replace("\\", "/"))
        p = os.path.join(DIR_NARR, name)
        if name and os.path.exists(p):
            out.append(p)
    return out


def resynth(d, text_only=False):
    for vid, items in TARGETS.items():
        ver = d.get("versions", {}).get(vid)
        if not ver:
            print("[tts] 版本 %s 不存在，跳过" % vid); continue
        by_idx = {str(s.get("idx")): s for s in ver.get("slides", [])}
        if text_only:
            print("[tts] %s：仅改文案，跳过合成" % vid); continue
        for idx, wav_name, seed in items:
            sl = by_idx.get(idx)
            if not sl:
                print("[tts] %s 无第 %s 页，跳过" % (vid, idx)); continue
            out = os.path.join(DIR_NARR, wav_name)
            ok = narrate.synth_one(sl.get("narration", ""), "中文", out,
                                   seed=seed, ref=config.VOICE_SAMPLE)
            print("[tts] %s 第%s页 -> %s : %s (%.1f KB)" %
                  (vid, idx, wav_name, "OK" if ok else "失败",
                   os.path.getsize(out) / 1024 if os.path.exists(out) else 0))
        # 重建全篇音频
        wavs = wav_list(ver, d)
        full_name = "full_narration.wav" if vid == "default" else ("full_%s.wav" % vid)
        full_path = os.path.join(DIR_NARR, full_name)
        r = narrate.concat_audio(wavs, full_path)
        print("[tts] %s 全篇音频：%s（%d 段，%.1f KB）" %
              (vid, "OK" if r else "失败", len(wavs),
               os.path.getsize(full_path) / 1024 if os.path.exists(full_path) else 0))
        ver["audio_full"] = "/api/audio?name=" + full_name


def main():
    text_only = "--text-only" in sys.argv
    backup()
    d = fix_text()
    fix_deck_text()
    fix_kb()
    resynth(d, text_only=text_only)
    # 残留检查
    left = 0
    bad = (OLD_ZH, OLD_ZH0, OLD_EN, OLD_EN2)
    for vid, ver in list(d.get("versions", {}).items()) + [("__top__", {"slides": d.get("slides", [])})]:
        for sl in ver.get("slides", []):
            t = (sl.get("narration") or "") + (sl.get("narration_en") or "")
            if any(x in t for x in bad) or (vid == "train2026" and OLD_ZH2 in t):
                left += 1
    print("[check] 残留旧文案页数：%d" % left)
    print("[done] 讲解署名已更新为「%s」" % NEW_ZH)


if __name__ == "__main__":
    main()
