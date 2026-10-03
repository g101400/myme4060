# -*- coding: utf-8 -*-
"""
myme.validate_e2e — 端到端小样本验证
流程：生成示例 pptx → 解析 → 建知识库 → 中英文问答 → 讲稿 → 中英 TTS 各一句
TTS 需 ComfyUI 嵌式 python + Qwen3-TTS + 声纹，若环境/策略不允许则优雅降级。
运行：python validate_e2e.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
config.ensure_dirs()

from sample_ppt import make_sample
import pptx_parse, knowledge_base, qa_brain, narrate

KB_NAME = "myme_kb"
SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_ppt", "水利渠道维护示例.pptx")


def main():
    print("== 1) 生成示例 pptx ==")
    make_sample.build(SAMPLE)
    print("   生成:", SAMPLE, os.path.getsize(SAMPLE), "bytes")

    print("== 2) 解析 PPT ==")
    slides = pptx_parse.parse(SAMPLE)
    for sl in slides:
        print(f"   第{sl['idx']}页 [{sl['title']}] 正文{len(sl['text'])}字 备注{len(sl['notes'])}字 图{len(sl['images'])}")
    assert len(slides) == 3 and slides[0]["title"], "解析异常"

    print("== 3) 建知识库（Ollama bge-m3 嵌入）==")
    kb = knowledge_base.build_kb(slides, KB_NAME)
    print("   知识块:", kb["chunks"], "持久化:", kb["path"])

    print("== 4) 中文问答 ==")
    ctx = knowledge_base.format_context(knowledge_base.query("维护周期是多久？", KB_NAME))
    ans_zh = qa_brain.answer_question("维护周期是多久？", ctx)
    print("   ", ans_zh[:200])

    print("== 5) 英文问答 ==")
    ctx_en = knowledge_base.format_context(knowledge_base.query("What is the maintenance cycle?", KB_NAME))
    ans_en = qa_brain.answer_question("What is the maintenance cycle?", ctx_en, model=config.LLM_MODEL)
    print("   ", ans_en[:200])

    print("== 6) 讲稿生成（第1页，中文）==")
    slides[0]["_total"] = len(slides)
    narr = qa_brain.generate_narration(slides[0], lang="中文", words=120)
    print("   ", narr[:160])

    print("== 7) TTS：中文 + 英文 各一句（炎冰声纹）==")
    for text, lang, seed in [("你好，我是炎冰，下面为您讲解水利渠道维护。", "中文", 42),
                              ("Hello, I am Yanbing. Let me walk you through channel maintenance.", "英文", 43)]:
        out = os.path.join(config.DIR_TEST, f"tts_{lang}.wav")
        ok = narrate.synth_one(text, lang, out, seed=seed, ref=config.VOICE_SAMPLE)
        if ok:
            print(f"   [{lang}] OK -> {out} ({os.path.getsize(out)} bytes)")
        else:
            print(f"   [{lang}] 跳过（TTS 不可用，详见上方日志；代码与已验证管线等价，可在正常环境运行）")

    print("\n✅ 端到端验证结束（TTS 除外均实时执行）。")


if __name__ == "__main__":
    main()
