# -*- coding: utf-8 -*-
"""
myme 便携启动器
==============
启动本地数字分身 Web 服务并自动打开浏览器。

- 开发模式：python myme_launcher.py  （WORKSPACE=源码目录，数据也落在源码目录）
- 打包模式：本文件作为 PyInstaller 入口，生成 Windows 便携 exe / MSI。
  冻结后运行时数据重定向到 %LOCALAPPDATA%/myme（见 config.py），保证持久化。

语音/数字人说明：本启动器仅负责"Web 服务 + 浏览器"。要让分身"开口说话"和生成视频，
需在本机另行运行 Ollama（含 bge-m3 / qwen3.5:9b 等模型）与常驻 TTS 服务
（ComfyUI 嵌式 python 的 tts_service.py）。两者就绪后，Web 内即可合成炎冰声音与口型视频。
"""
import os
import sys
import time
import threading
import urllib.request
import webbrowser
import traceback

import config
import app

# 统一输出编码为 UTF-8（Windows 控制台/cp1252 无法打印中文，会导致启动崩溃）
for _s in (sys.stdout, sys.stderr):
    if _s is not None:
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _wait_and_open():
    url = "http://127.0.0.1:%d/" % app.PORT
    # 轮询直到服务可访问，再打开浏览器（最多等 ~15s）
    for _ in range(30):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _err_log(msg):
    try:
        d = getattr(config, "DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(d, "myme_error.log"), "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        config.ensure_dirs()
        threading.Thread(target=_wait_and_open, daemon=True).start()
        app.run()
    except Exception:
        _err_log(traceback.format_exc())
        raise
