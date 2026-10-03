# -*- coding: utf-8 -*-
"""
myme.llm_client — 统一大模型接入层（纯标准库 urllib）

目标：把"模型接入"从写死的 Ollama 地址解耦，改为可在「设置 → 大模型接入」里配置，
既支持本地模型（Ollama，默认 http://localhost:11434），
也支持线上 OpenAI-compatible 服务（DeepSeek / 通义千问 / 自建 vLLM 等）。

配置落盘在 <DATA_DIR>/llm_config.json，结构见 DEFAULT_LLM_CONFIG。
对外两个主函数：
  - chat(messages, model=None, temperature=None, max_tokens=None, cfg=None)
        走 OpenAI-compatible /chat/completions（Ollama 走 /api/chat）。
  - embed(text, model=None, cfg=None)
        走 OpenAI-compatible /v1/embeddings（Ollama 走 /api/embeddings）。
未配置时回落到 Ollama 本地默认值，保证旧行为零破坏（知识库 / 答疑照常工作）。
"""
import os
import json
import urllib.request
import urllib.error
import config


LLM_CONFIG_FILE = os.path.join(config.DATA_DIR, "llm_config.json")

DEFAULT_LLM_CONFIG = {
    "enabled": True,
    "provider": "ollama",          # ollama | openai（OpenAI-compatible）
    "base_url": "http://localhost:11434",
    "api_key": "",
    "chat_model": "qwen3.5:9b",
    "embed_model": "bge-m3",
    "temperature": 0.7,
    "max_tokens": 1200,
    "timeout": 300,
    "extra": {},                   # 其他必须参数（org / project / 自定义 header 等），预留扩展
}


def load_llm_config():
    cfg = dict(DEFAULT_LLM_CONFIG)
    try:
        if os.path.exists(LLM_CONFIG_FILE):
            with open(LLM_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
    except Exception:
        pass
    return cfg


def save_llm_config(patch):
    cur = load_llm_config()
    if isinstance(patch, dict):
        cur.update(patch)
    try:
        os.makedirs(os.path.dirname(LLM_CONFIG_FILE), exist_ok=True)
        with open(LLM_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return cur


def _post_json(url, payload, timeout=300, api_key=""):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat(messages, model=None, temperature=None, max_tokens=None, cfg=None):
    """对话补全。返回纯文本。

    provider=ollama  -> POST {base_url}/api/chat
    provider=openai  -> POST {base_url}/v1/chat/completions（兼容大多线上服务）
    """
    cfg = cfg or load_llm_config()
    model = model or cfg.get("chat_model") or "qwen3.5:9b"
    temperature = temperature if temperature is not None else float(cfg.get("temperature", 0.7))
    max_tokens = max_tokens or int(cfg.get("max_tokens", 1200))
    timeout = int(cfg.get("timeout", 300))
    base = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
    if cfg.get("provider") == "ollama":
        payload = {
            "model": model, "messages": messages,
            # qwen3 默认开启 thinking 会把回答塞进 thinking 字段导致 content 为空；
            # 数字分身答疑/讲稿/生成 PPT 均需直接产出文本，关闭思考更快更稳。
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
            "stream": False,
        }
        r = _post_json(f"{base}/api/chat", payload, timeout)
        return (r.get("message") or {}).get("content", "").strip()
    # OpenAI-compatible（线上 / vLLM / llama.cpp 等）
    payload = {
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens, "stream": False,
    }
    if isinstance(cfg.get("extra"), dict):
        payload.update(cfg["extra"])
    r = _post_json(f"{base}/v1/chat/completions", payload, timeout, cfg.get("api_key", ""))
    return (r.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def embed(text, model=None, cfg=None):
    """文本向量化。返回 list[float]。"""
    cfg = cfg or load_llm_config()
    model = model or cfg.get("embed_model") or "bge-m3"
    timeout = int(cfg.get("timeout", 300))
    base = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
    if cfg.get("provider") == "ollama":
        r = _post_json(f"{base}/api/embeddings", {"model": model, "prompt": text}, timeout)
        return r["embedding"]
    r = _post_json(f"{base}/v1/embeddings", {"model": model, "input": text}, timeout, cfg.get("api_key", ""))
    return r["data"][0]["embedding"]
