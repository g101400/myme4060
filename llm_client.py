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
import hashlib
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


# —— 嵌入结果缓存（矢量是确定性的，同 model+同文本必然同结果）——
# 【2026-10-07 实测】本机 Ollama 在「刚被大语言模型占用过之后」要切回 bge-m3，
# 一次冷加载约 60s；同样的 Query 再来一遍就是 0.1s。答疑/检索里同一个问句会被反复问
# （多查询扩展拆出的子问法彼此相近、UI 上同问题重问），每次都吃这 60s 很难看。
# 这里按 (model, 文本指纹) 缓存，上限 4096 条（1024 维 float 列表约 40KB/条 → 上限约 160MB）。
_EMBED_CACHE = {}
_EMBED_CACHE_MAX = 4096


def embed(text, model=None, cfg=None):
    """文本向量化。命中缓存直接返回（同 model + 同文本），否则走本地 Ollama。"""
    cfg = cfg or load_llm_config()
    model = model or cfg.get("embed_model") or "bge-m3"
    if cfg.get("provider") != "ollama" and not cfg.get("provider"):
        pass  # 未显式指定 provider 时也兼容走 ollama 分支（保持原行为）
    key = (model, hashlib.md5((text or "").encode("utf-8", "ignore")).hexdigest())
    hit = _EMBED_CACHE.get(key)
    if hit is not None:
        return hit
    timeout = int(cfg.get("timeout", 300))
    base = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
    if cfg.get("provider") == "ollama":
        r = _post_json(f"{base}/api/embeddings", {"model": model, "prompt": text}, timeout)
        vec = r["embedding"]
    else:
        r = _post_json(f"{base}/v1/embeddings", {"model": model, "input": text}, timeout, cfg.get("api_key", ""))
        vec = r["data"][0]["embedding"]
    if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
        _EMBED_CACHE.clear()          # 简单淘汰：满了整表清（上限 4096，清一次代价可接受）
    _EMBED_CACHE[key] = vec
    return vec
