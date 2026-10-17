# -*- coding: utf-8 -*-
"""myme 播放端本地静态服务（统信 UOS / Deepin / 麒麟）。

为什么必须起 http 而不是直接 file:// 打开：浏览器的同源策略会拦掉 file:// 下的
fetch('manifest.json')，播放器读不到清单就只能白屏。走 127.0.0.1 本机 http 最省事。

v2.9.3 加固（2026-10-16）：
  · 新增 **/__myme_ping__ 探针**：启动器靠它判断"服务真的就绪"，不再靠 curl。
    只认探针里的 magic token，所以任何"别的服务占着端口 / 代理替我回了 502"
    都不会被误判成就绪（这是旧版"点图标白屏"的第一根因）。
  · 端口从"服务自己往后找"改成"启动器先找好空闲端口再传进来"，
    探针目标与真实端口不再可能错位。
  · 起不来时把原因写进 stderr（启动器会落盘到 /tmp/myme-player.log），不静默。
"""
import functools
import http.server
import os
import socketserver
import sys
import tempfile

PING_PATH = "/__myme_ping__"
PING_TOKEN = "myme-player-ok"
VERSION = "@VER@"

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7862
ROOT = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))


class H(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        p = super().translate_path(path)
        # 把请求路径钉在 ROOT 下，避免越权读取
        try:
            rel = os.path.relpath(p, ROOT)
        except Exception:
            rel = ""
        if rel.startswith(".."):
            p = os.path.join(ROOT, "index.html")
        return p

    def do_GET(self):
        # 就绪探针：只回 magic token，杜绝"端口上蹲着别的服务"被误判成就绪
        if self.path.split("?", 1)[0] == PING_PATH:
            body = ("%s v%s" % (PING_TOKEN, VERSION)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_HEAD(self):
        if self.path.split("?", 1)[0] == PING_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(PING_TOKEN) + 8))
            self.end_headers()
            return
        return super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _write_port_file(port):
    """把实际端口写到临时目录，供启动器/排障脚本读取。"""
    d = tempfile.gettempdir()
    try:
        with open(os.path.join(d, "myme-player.port"), "w") as f:
            f.write(str(port))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        os.chdir(ROOT)
    except OSError as e:
        sys.stderr.write("myme 播放服务：无法进入目录 %s（%s）\n" % (ROOT, e))
        raise SystemExit(2)
    if not os.path.isfile(os.path.join(ROOT, "player.html")):
        # 不是致命错误（有些老包没有 player.html），但必须留下线索
        sys.stderr.write("myme 播放服务：警告 —— %s 下没有 player.html\n" % ROOT)
    try:
        httpd = S(("127.0.0.1", PORT), functools.partial(H, directory=ROOT))
    except OSError as e:
        sys.stderr.write("myme 播放服务：端口 %d 无法监听（%s）\n" % (PORT, e))
        raise SystemExit(3)
    httpd.port = PORT
    _write_port_file(PORT)
    print("myme 播放端已启动： http://127.0.0.1:%d/player.html" % PORT)
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
