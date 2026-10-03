# -*- coding: utf-8 -*-
"""
myme.asr_listen — 语音转写工作器（由 ComfyUI 嵌式 python 运行，自带 torch+ffmpeg）
把用户上传的语音问题转成文字，支持中英混合(auto 检测)。
- transcribe(audio, lang)：供 app.py 调用的函数，自动经嵌式 python 跑 whisper
- 命令行：python asr_listen.py --audio question.wav [--lang auto|zh|en]  （嵌式 python）
  首次使用需在嵌式 python 安装 openai-whisper：
  D:/ComfyUI_Mie_2026_V8.0/python_embeded/python.exe -m pip install openai-whisper
"""
import sys, os, argparse, subprocess
from config import EMBEDDED_PY, FFMPEG

for _n in ("stdout", "stderr"):
    _o = getattr(sys, _n)
    try:
        if getattr(_o, "encoding", "") != "utf-8":
            import io
            setattr(sys, _n, io.TextIOWrapper(_o.buffer, encoding="utf-8", errors="replace"))
    except Exception:
        pass


def to_wav16k(audio, tmp):
    subprocess.run([FFMPEG, "-y", "-i", audio, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", tmp],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return tmp


def _local_transcribe(audio, lang="auto"):
    """在当前 python 直接跑 whisper（仅当已装 whisper/torch）。"""
    import whisper
    wav16k = audio + ".16k.wav"
    to_wav16k(audio, wav16k)
    try:
        model = whisper.load_model("base")
        res = model.transcribe(wav16k, language=(None if lang == "auto" else lang), fp16=False)
        return (res.get("text") or "").strip()
    finally:
        if os.path.exists(wav16k):
            os.remove(wav16k)


def transcribe(audio, lang="auto"):
    """转写入口：本 python 无 whisper 时，自动经嵌式 python 运行本脚本。"""
    # 优先尝试本进程
    try:
        import whisper  # noqa
        return _local_transcribe(audio, lang)
    except Exception:
        pass
    # 回退：经嵌式 python 跑 whisper
    if not os.path.exists(EMBEDDED_PY):
        return "（未配置嵌式 python，无法使用语音识别）"
    if not os.path.exists(audio):
        return "（音频文件缺失）"
    cmd = [EMBEDDED_PY, os.path.abspath(__file__), "--audio", audio, "--lang", lang]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)
    if r.returncode != 0:
        return f"（语音识别失败：{r.stderr[-300:]}）"
    return (r.stdout or "").strip()


def main():
    AP = argparse.ArgumentParser()
    AP.add_argument("--audio", required=True)
    AP.add_argument("--lang", default="auto")
    A = AP.parse_args()
    try:
        import whisper
    except Exception:
        print("[asr] 未安装 openai-whisper。请先在嵌式 python 执行: -m pip install openai-whisper", flush=True)
        sys.exit(3)
    if not os.path.exists(A.audio):
        print(f"[asr] 音频缺失: {A.audio}", flush=True); sys.exit(2)
    print(_local_transcribe(A.audio, A.lang), flush=True)


if __name__ == "__main__":
    main()
