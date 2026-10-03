# -*- coding: utf-8 -*-
"""
myme.qa_brain — 大模型大脑（统一接入层 llm_client，纯标准库 urllib）
- answer_question：基于 RAG 上下文回答 PPT 相关问题（中英双语、超范围拒答）
- generate_narration：把单页内容生成口语化讲稿（供 TTS 朗读）

模型接入由「设置 → 大模型接入」配置（地址/密钥/模型），可接本地(Ollama)也可接线上模型，
无需改代码。知识向量库即借由此处的大模型提升答疑质量。
"""
import json
import urllib.request
from config import OLLAMA_BASE, LLM_MODEL
import llm_client
from prompts.system_prompt import build_qa_messages, build_narrate_messages


def _post_json(url, payload, timeout=300):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def ollama_chat(messages, model=LLM_MODEL, temperature=0.7, max_tokens=1200):
    """对话补全：统一走 llm_client（Ollama 本地 / OpenAI-compatible 线上皆可）。"""
    return llm_client.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)


def answer_question(question: str, context: str, model=LLM_MODEL, lang: str = None):
    """RAG 问答：注入检索上下文 + 双语系统提示词。

    lang 为 None 时回答语种自动跟随提问语种；
    lang 传 "中文"/"English" 则强制以该语种作答（提问仍可为另一语种，要求先理解再翻译作答）。
    """
    messages = build_qa_messages(question, context, lang=lang)
    return ollama_chat(messages, model=model, temperature=0.3, max_tokens=900)


def generate_narration(slide, lang="中文", words=200, model=LLM_MODEL):
    """把一页 PPT 生成口语化讲解词。"""
    messages = build_narrate_messages(
        slide["idx"], slide.get("_total", "?"), slide.get("title", ""),
        slide.get("text", ""), slide.get("notes", ""), lang=lang, words=words)
    return ollama_chat(messages, model=model, temperature=0.6, max_tokens=words * 2)


if __name__ == "__main__":
    ctx = "【PPT第1页 水渠道维护】水渠道常规维护周期为每季度一次，汛期前后需额外专项巡检。"
    print(answer_question("维护周期是多久？", ctx))
    print("---EN---")
    print(answer_question("What is the maintenance cycle?", ctx))
