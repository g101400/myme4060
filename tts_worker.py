# -*- coding: utf-8 -*-
"""
myme.tts_worker — 声音克隆 TTS 工作器（必须由 ComfyUI 嵌式 python 运行）
复用已验证管线：Qwen3-TTS-1.7B + 炎冰声纹(yanbing-sample-01.wav)。
用法（嵌式 python）：
  python tts_worker.py --text "..." --lang Chinese --out out.wav [--ref 声纹.wav] [--seed 42]
输出 24kHz 单声道 wav。
"""
import sys, os, time, argparse
# 强制 stdout/stderr UTF-8，避免中文路径/文本乱码
for _n in ("stdout", "stderr"):
    _o = getattr(sys, _n)
    try:
        if getattr(_o, "encoding", "") != "utf-8":
            import io
            setattr(sys, _n, io.TextIOWrapper(_o.buffer, encoding="utf-8", errors="replace"))
    except Exception:
        pass

import numpy as np

# ===== 路径（与 clone_narr.py 一致）=====
HERE = os.path.dirname(os.path.abspath(__file__))
# 隔离的 transformers（qwen_tts 官方要求 ==4.57.3）。仅在存在时前置到 sys.path，
# 让本 TTS 子进程使用它，而不动 ComfyUI 自带的 transformers 5.5（非破坏、可回退）。
TS_DEPS = os.environ.get("MYME_TS_DEPS") or os.path.join(HERE, "ts_deps")
if os.path.isdir(TS_DEPS) and TS_DEPS not in sys.path:
    sys.path.insert(0, TS_DEPS)
# 【2026-10-03 修复】原写死 D:\ComfyUI_Mie_2026_V8.0，本机实际在 H 盘 → 本子进程 import qwen_tts 必崩。
# 改为「环境变量 > 盘符扫描」探测（本文件跑在 ComfyUI 嵌式 python 下，自带 numpy，不引 config 以免副作用）。
def _probe_comfy_root():
    try:
        import string
        roots = [d + ":" for d in string.ascii_uppercase]
        for drv in sorted(roots, key=lambda d: (0 if d[0] not in ("C", "D") else 1, d)):
            for buf in (os.path.join(drv, "ComfyUI_Mie_2026_V8.0"),
                        os.path.join(drv, "ComfyUI")):
                if os.path.isdir(buf):
                    return buf
    except Exception:
        pass
    return ""


COMFYUI_ROOT = (os.environ.get("MYME_COMFYUI_ROOT") or "").strip() or _probe_comfy_root()
QWEN_TTS_NODES = os.path.join(COMFYUI_ROOT, "custom_nodes", "qwen3-tts-comfyui") if COMFYUI_ROOT else ""
QWEN_TTS_MODEL = os.path.join(COMFYUI_ROOT, "models", "qwen-tts", "Qwen3-TTS-12Hz-1.7B-Base") if COMFYUI_ROOT else ""
VOICE_SAMPLE = os.path.join(HERE, "voice", "yanbing-sample-01.wav")
FF = r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"

sys.path.insert(0, QWEN_TTS_NODES)


def _patched_eager_attention_forward(module, query, key, value, attention_mask, scaling, dropout=0.0, **kwargs):
    # 修复 transformers 5.x 下 qwen_tts 自定义注意力掩码形状不匹配
    # （报错 "size a (21) must match b (11)" at non-singleton dimension 3）。
    #
    # 成因：qwen_tts 在「带 KV-cache 的分组预填」场景下，transformers 5.x 生成的 4D 因果掩码形状
    #      与 attn_weights 的实际 [Q,K] 不一致。例如实测：attn=(1,16,11,21) key=(1,16,21,128)
    #      但 attention_mask=(1,1,1,11) —— 11 个新查询对 21 个缓存键，却拿到单行 11 键的掩码。
    #      原实现 `causal_mask = attention_mask[..., :, :key_states.shape[-2]]` 只裁 K 维、不补 Q 维，
    #      相加即报形状不匹配。
    #
    # 修复策略：若传入掩码形状与 attn_weights 完全一致，直接采用（已正确编码因果/填充）；
    #          若不一致（分组预填+缓存场景），按真实 Q/K 重建加法式因果掩码：
    #          第 i 个新查询(全局位置 prev+i) 仅可见键 [0, prev+i]，prev = K - Q（已缓存键数）。
    import torch
    import torch.nn.functional as F
    from qwen_tts.core.models.modeling_qwen3_tts import repeat_kv
    key_states = repeat_kv(key, module.num_key_value_groups)
    value_states = repeat_kv(value, module.num_key_value_groups)
    attn_weights = torch.matmul(query, key_states.transpose(2, 3)) * scaling
    if attention_mask is not None:
        q_len = attn_weights.size(-2)
        k_len = attn_weights.size(-1)
        am = attention_mask
        if am.size(-2) == q_len and am.size(-1) == k_len:
            causal_mask = am
        else:
            device = attn_weights.device
            prev = k_len - q_len  # 已缓存键数
            idx_i = torch.arange(q_len, device=device)[:, None]
            idx_j = torch.arange(k_len, device=device)[None, :]
            block = idx_j > (prev + idx_i)  # 超出因果可见范围的键置 -inf
            causal_mask = torch.zeros(q_len, k_len, device=device, dtype=attn_weights.dtype)
            causal_mask = causal_mask.masked_fill(block, torch.finfo(attn_weights.dtype).min)
            causal_mask = causal_mask[None, None, :, :]
        attn_weights = attn_weights + causal_mask
    attn_weights = F.softmax(attn_weights, dim=-1, dtype=torch.float32).to(query.dtype)
    attn_weights = F.dropout(attn_weights, p=dropout, training=module.training)
    attn_output = torch.matmul(attn_weights, value_states)
    attn_output = attn_output.transpose(1, 2).contiguous()
    return attn_output, attn_weights


def _patch_qwen_tts_mask():
    # 不改动共享 ComfyUI 节点文件：在运行时把 qwen_tts 模块内的 eager_attention_forward
    # 替换为上面的对齐版本。该函数被本模块内注意力层以全局名引用，运行时替换即可生效。
    try:
        import qwen_tts.core.models.modeling_qwen3_tts as _mt
        _mt.eager_attention_forward = _patched_eager_attention_forward
        print("[tts] patched qwen_tts eager_attention_forward (mask-align)", flush=True)
    except Exception as _e:
        print(f"[tts] patch skip: {_e}", flush=True)


def load_model():
    import torch
    # 修复 transformers/tokenizers 版本不兼容：高版本 tokenizers 移除了 backend_tokenizer 属性，
    # 加载 tokenizer 时 _patch_mistral_regex 会抛 AttributeError。Qwen 分词器非 mistral，跳过补丁无害。
    try:
        import transformers.tokenization_utils_tokenizers as _tutf
        # 高版本 tokenizers 移除了 backend_tokenizer 属性，原 _patch_mistral_regex 在加载
        # tokenizer 时会抛 AttributeError。该函数仅用于 mistral 类分词器的正则补丁，
        # Qwen 分词器不需要，直接跳过、原样返回 tokenizer 即可。
        def _skip_patch(self, tokenizer, *args, **kwargs):
            return tokenizer
        _tutf.PreTrainedTokenizerFast._patch_mistral_regex = _skip_patch
    except Exception:
        pass
    from qwen_tts import Qwen3TTSModel
    _patch_qwen_tts_mask()
    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(QWEN_TTS_MODEL, device_map="cuda", dtype=torch.bfloat16)
    # transformers 5.x 默认 sdpa 对 Qwen3-TTS 自定义模型有形状 bug；
    # 模型自带正确的 eager_attention_forward，只需把各 config 的 _attn_implementation 设为 eager。
    _force_eager_attention(model)
    print(f"[tts] model loaded {time.time()-t0:.1f}s", flush=True)
    return model


def _force_eager_attention(model):
    # 递归把所有 config 的 _attn_implementation 改为 eager，让模型用自带的 eager_attention_forward，
    # 避开 transformers 5.x 的 SDPA 集成形状不兼容（[16,21] vs [16,11]）。
    seen = set()
    def walk(o):
        if o is None or id(o) in seen:
            return
        seen.add(id(o))
        att = getattr(o, "_attn_implementation", None)
        if isinstance(att, str) and att != "eager":
            try:
                object.__setattr__(o, "_attn_implementation", "eager")
            except Exception:
                pass
        for k in ("config", "model", "talker", "code_predictor", "text_config",
                  "talker_config", "code_predictor_config"):
            walk(getattr(o, k, None))
    # Qwen3TTSModel 非标准 nn.Module，无 .modules()；从模型与其 config 出发递归即可覆盖子配置
    walk(model)
    walk(getattr(model, "config", None))
    cfg = getattr(model, "config", None)
    if cfg is not None:
        for name in ("talker_config", "code_predictor_config", "text_config"):
            walk(getattr(cfg, name, None))


def synth(model, text, lang, ref, seed, top_p=0.8, top_k=20, temp=0.9, rep_pen=1.05, max_tokens=4096):
    import torch
    torch.manual_seed(seed)
    wavs, sr = model.generate_voice_clone(
        text=text, language=lang, ref_audio=ref,
        x_vector_only_mode=True, max_new_tokens=max_tokens,
        top_p=top_p, top_k=top_k, temperature=temp, repetition_penalty=rep_pen,
    )
    wav = np.asarray(wavs[0] if isinstance(wavs, list) else wavs, dtype=np.float32)
    if wav.ndim > 1:
        wav = wav.mean(-1)
    return wav, int(sr)


def to_24k_mono(wav, sr, out):
    import soundfile as sf
    tmp = out + ".raw.wav"
    sf.write(tmp, wav, sr)
    # 统一 24kHz 单声道，便于前端/合成
    import subprocess
    subprocess.run([FF, "-y", "-i", tmp, "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    if os.path.exists(tmp):
        os.remove(tmp)


def main():
    AP = argparse.ArgumentParser()
    AP.add_argument("--text", required=True)
    AP.add_argument("--lang", default="Chinese", choices=["Chinese", "English"])
    AP.add_argument("--out", required=True)
    AP.add_argument("--ref", default=VOICE_SAMPLE)
    AP.add_argument("--seed", type=int, default=42)
    AP.add_argument("--max_tokens", type=int, default=4096)
    A = AP.parse_args()

    if not os.path.exists(A.ref):
        print(f"[tts] 声纹缺失: {A.ref}", flush=True); sys.exit(2)

    model = load_model()
    t0 = time.time()
    wav, sr = synth(model, A.text, A.lang, A.ref, A.seed, max_tokens=A.max_tokens)
    os.makedirs(os.path.dirname(os.path.abspath(A.out)), exist_ok=True)
    to_24k_mono(wav, sr, A.out)
    print(f"[tts] done {os.path.basename(A.out)} {len(wav)/sr:.2f}s gen={time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
