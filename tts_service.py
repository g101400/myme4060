# -*- coding: utf-8 -*-
"""
myme.tts_service — 常驻 TTS 服务（必须由 ComfyUI 嵌式 python 运行）
加载 Qwen3-TTS 模型一次并常驻内存；通过 HTTP 接收文本，返回 24k 单声道 wav。
这样 app.py / narrate.py 调用时不再每次冷加载模型（原本每次数分钟）。

运行（嵌式 python）：
  python tts_service.py [--host 127.0.0.1] [--port 8777] [--eager] [--lazy]
  --eager  启动即加载模型（默认，常驻场景推荐）；--lazy 首次请求时再加载。

端点：
  GET  /ready                       -> {"ready": true/false, "loading": ...}
  POST /tts  body={text,lang,out,seed,ref}
       -> 合成并写 out（24k 单声道 wav），返回 {"ok":true,"dur":秒,"out":路径}
          lang: "Chinese"/"English"
设计：ThreadingHTTPServer + 全局锁，保证 GPU 上串行生成（避免并发打乱模型状态）。
"""
import os
import sys
import json
import time
import argparse
import threading
import queue
import http.server
from urllib.parse import urlparse

# 嵌式 python 的 _pth 不会自动把脚本所在目录加入 sys.path，须手动加入才能 import 同目录模块。
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 复用 tts_worker 的路径装配（ts_deps 隔离 transformers + qwen_tts 节点目录）
# 以及 load_model / synth / to_24k_mono / 声纹默认值。
import tts_worker as _tw

MODEL = None
LOADING = False
LOCK = threading.Lock()
# 推理请求队列：HTTP 工作线程仅入队，主线程串行消费（CUDA 推理只在主线程执行，
# 避免工作线程调用 GPU 时卡死并永久占用 LOCK，导致整个服务 /tts 全部阻塞）。
REQ_Q = queue.Queue()


def _send(handler, code, obj, ctype="application/json; charset=utf-8"):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8") if isinstance(obj, (dict, list)) else (
        obj.encode("utf-8") if isinstance(obj, str) else obj)
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def get_model():
    """惰性加载（仅 --lazy 模式用到）。"""
    global MODEL, LOADING
    if MODEL is not None:
        return MODEL
    with LOCK:
        if MODEL is None and not LOADING:
            LOADING = True
            try:
                MODEL = _tw.load_model()
            finally:
                LOADING = False
    return MODEL


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/ready":
            _send(self, 200, {"ready": MODEL is not None, "loading": LOADING,
                              "model": os.path.basename(_tw.QWEN_TTS_MODEL)})
        else:
            _send(self, 404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path)
        if p.path != "/tts":
            _send(self, 404, {"error": "unknown endpoint"}); return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            _send(self, 400, {"error": "bad json"}); return
        text = (data.get("text") or "").strip()
        if not text:
            _send(self, 400, {"error": "empty text"}); return
        lang = data.get("lang", "Chinese")
        out = data.get("out")
        if not out:
            _send(self, 400, {"error": "missing out"}); return
        seed = int(data.get("seed", 42))
        ref = data.get("ref") or _tw.VOICE_SAMPLE
        if not os.path.exists(ref):
            _send(self, 400, {"error": f"声纹缺失: {ref}"}); return
        try:
            ev = threading.Event()
            req = {"text": text, "lang": lang, "out": out, "ref": ref, "seed": seed,
                   "event": ev, "result": None, "error": None}
            REQ_Q.put(req)
            if not ev.wait(timeout=600):
                _send(self, 504, {"error": "TTS 超时（推理卡住，需重启服务）"}); return
            if req["error"] is not None:
                _send(self, 500, {"error": req["error"]}); return
            _send(self, 200, req["result"])
        except Exception as e:
            _send(self, 500, {"error": str(e)})


def _process(req):
    """主线程消费：执行一次 GPU 推理并写文件（CUDA 只在主线程调用）。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(req["out"])), exist_ok=True)
        m = get_model()
        if m is None:
            req["error"] = "model not loaded"; return
        t0 = time.time()
        wav, sr = _tw.synth(m, req["text"], req["lang"], req["ref"], req["seed"])
        _tw.to_24k_mono(wav, sr, req["out"])
        dur = len(wav) / sr if sr else 0
        req["result"] = {"ok": True, "dur": round(dur, 2),
                         "gen": round(time.time() - t0, 1), "out": req["out"]}
    except Exception as e:
        req["error"] = str(e)


def run(host="127.0.0.1", port=8777, eager=True):
    if eager:
        print("[tts_service] eager loading model (one-time)…", flush=True)
        get_model()
        print(f"[tts_service] model ready, listening on http://{host}:{port}", flush=True)
    else:
        print(f"[tts_service] lazy mode, listening on http://{host}:{port}", flush=True)
    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 主线程串行消费推理队列（CUDA 推理只在主线程执行，避免工作线程调用 GPU 卡死/锁死）
    while True:
        try:
            req = REQ_Q.get()
            if req is None:
                break
            _process(req)
            req["event"].set()
        except Exception:
            try:
                if req is not None:
                    req["event"].set()
            except Exception:
                pass


if __name__ == "__main__":
    AP = argparse.ArgumentParser()
    AP.add_argument("--host", default="127.0.0.1")
    AP.add_argument("--port", type=int, default=8777)
    AP.add_argument("--eager", dest="eager", action="store_true", default=True)
    AP.add_argument("--lazy", dest="eager", action="store_false")
    A = AP.parse_args()
    run(A.host, A.port, A.eager)
