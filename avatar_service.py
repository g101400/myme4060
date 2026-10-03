# -*- coding: utf-8 -*-
"""
myme.avatar_service — 常驻数字人服务（必须由 ComfyUI 嵌式 python 运行）
加载 LivePortrait 推理模型一次并常驻内存；通过 HTTP 接收（音频路径 + 肖像路径）
-> 返回音频驱动口型的 mp4（炎冰声纹 + 视频数字人）。模型只加载一次。

运行（嵌式 python）：
  python avatar_service.py [--host 127.0.0.1] [--port 8778] [--eager] [--lazy] [--cpu]

端点：
  GET  /ready                 -> {"ready": true/false, "loading": ...}
  POST /avatar  body={audio, image, out, res, fps}
       -> 合成口型同步 mp4，返回 {"ok":true,"out_mp4":路径,"note":...}
设计：ThreadingHTTPServer + 全局锁，保证 GPU 上串行生成。
"""
import os
import sys
import json
import time
import argparse
import threading
import http.server
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import avatar as _av

MODEL = None
LOADING = False
LOCK = threading.Lock()


def _send(handler, code, obj, ctype="application/json; charset=utf-8"):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8") if isinstance(obj, (dict, list)) else (
        obj.encode("utf-8") if isinstance(obj, str) else obj)
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def get_driver():
    global MODEL, LOADING
    if MODEL is not None:
        return MODEL
    with LOCK:
        if MODEL is None and not LOADING:
            LOADING = True
            try:
                MODEL = _av.get_driver(force_cpu=bool(os.environ.get("MYME_AVATAR_CPU")))
            finally:
                LOADING = False
    return MODEL


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/ready":
            _send(self, 200, {"ready": MODEL is not None, "loading": LOADING})
        else:
            _send(self, 404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path)
        if p.path != "/avatar":
            _send(self, 404, {"error": "unknown endpoint"}); return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            _send(self, 400, {"error": "bad json"}); return
        audio = data.get("audio") or ""
        image = data.get("image") or ""
        out = data.get("out") or ""
        res = data.get("res") or ""
        fps = int(data.get("fps", 25) or 25)
        motion = data.get("motion")
        motion = 1.0 if motion is None else float(motion)
        if not audio or not out:
            _send(self, 400, {"error": "missing audio/out"}); return
        if not os.path.exists(audio):
            _send(self, 400, {"error": f"音频缺失: {audio}"}); return
        try:
            with LOCK:
                d = get_driver()
                if d is None:
                    _send(self, 503, {"error": "model not loaded"}); return
                t0 = time.time()
                r = d.drive(audio, image, out, fps=fps, motion=motion)
                r["gen"] = round(time.time() - t0, 1)
            _send(self, 200, r)
        except Exception as e:
            _send(self, 500, {"error": str(e)})


def run(host="127.0.0.1", port=8778, eager=True, force_cpu=False):
    if force_cpu:
        os.environ["MYME_AVATAR_CPU"] = "1"
    if eager:
        print("[avatar_service] eager loading LivePortrait (one-time, ~1min)…", flush=True)
        get_driver()
        print(f"[avatar_service] model ready, listening on http://{host}:{port}", flush=True)
    else:
        print(f"[avatar_service] lazy mode, listening on http://{host}:{port}", flush=True)
    srv = http.server.ThreadingHTTPServer((host, port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    AP = argparse.ArgumentParser()
    AP.add_argument("--host", default="127.0.0.1")
    AP.add_argument("--port", type=int, default=8778)
    AP.add_argument("--eager", dest="eager", action="store_true", default=True)
    AP.add_argument("--lazy", dest="eager", action="store_false")
    AP.add_argument("--cpu", dest="force_cpu", action="store_true", default=False)
    A = AP.parse_args()
    run(A.host, A.port, A.eager, A.force_cpu)
