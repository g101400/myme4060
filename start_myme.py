# -*- coding: utf-8 -*-
"""
myme.start_myme — 一键启动编排器（建议由 start_myme.bat 调用）
顺序：① 常驻 TTS 服务（热模型，秒级合成） ② 常驻数字人服务（热 LivePortrait）
      ③ Web 应用；退出时（Ctrl+C / 关闭窗口）一起收尾，避免残留后台进程。

设计要点：
- 三个子进程都本编排器统一拉起与回收，app.py 自身检测到服务已在跑会跳过自启，不冲突。
- 嵌入式 python（TTS/数字人，自带 torch/insightface/cv2）跑重模型；系统 python 跑 Web。
- 每个子进程日志落在 DIR_TEST/<name>.log，便于排查。
- 退出清理用 "taskkill /T /F" 兜底，确保整个进程树（含孙进程）都被回收。
"""
import os
import sys
import time
import signal
import atexit
import subprocess
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import config as C

CHILDREN = []          # 需要统一回收的子进程
_LOG_LOCK = None


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _spawn(cmd, log_path, name):
    """后台拉起子进程（无窗口），记录到 CHILDREN 与日志。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    logf = open(log_path, "w", encoding="utf-8", buffering=1)
    _log(f"启动 {name}: {' '.join(cmd)}")
    try:
        p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags, cwd=HERE)
    except Exception as e:
        _log(f"[!] {name} 启动失败: {e}")
        logf.close()
        return None
    CHILDREN.append((name, p, logf))
    _log(f"  -> PID {p.pid}，日志 {log_path}")
    return p


def _probe(url, timeout=180, interval=2.0):
    """轮询 HTTP 就绪端点，返回 True/False。"""
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = str(e)
        except Exception as e:
            last_err = str(e)
        time.sleep(interval)
    if last_err:
        _log(f"  (探测超时，末次错误: {last_err[:120]})")
    return False


def _is_ready(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _free_ports(ports):
    """启动前释放可能残留的监听端口（上一次异常退出留下的后台进程）。
    用 netstat 找到占用端口的 PID 并 taskkill。仅在我们尚未拉起子进程时调用，安全。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, encoding="utf-8",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return
    for port in ports:
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                if pid.isdigit():
                    _log(f"释放残留端口 {port} (PID {pid}) …")
                    subprocess.run(["taskkill", "/PID", pid, "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def cleanup():
    """回收所有子进程树。幂等，可被多次调用。"""
    for name, p, logf in list(CHILDREN):
        if p.poll() is None:
            _log(f"收尾 {name} (PID {p.pid}) …")
            try:
                # 优先 taskkill 整棵进程树（含孙进程），再 Python 侧兜底
                subprocess.run(
                    ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass
        try:
            logf.close()
        except Exception:
            pass
    CHILDREN.clear()


def _signal_handler(signum, frame):
    _log(f"收到信号 {signum}，开始收尾…")
    cleanup()
    os._exit(0)


def main():
    atexit.register(cleanup)
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _signal_handler)
        except Exception:
            pass

    ensure_dirs_log()

    # ① 常驻 TTS 服务（热模型）
    if C.USE_TTS_SERVICE:
        if _is_ready(C.TTS_SERVICE_URL + "/ready"):
            _log(f"TTS 服务已在运行：{C.TTS_SERVICE_URL}（热合成就绪）")
        else:
            _spawn([C.EMBEDDED_PY, os.path.join(HERE, "tts_service.py"),
                    "--host", "127.0.0.1", "--port", str(C.TTS_SERVICE_PORT), "--eager"],
                   os.path.join(C.DIR_TEST, "tts_service.log"), "TTS 服务")
            ok = _probe(C.TTS_SERVICE_URL + "/ready", timeout=240)
            _log("TTS 服务就绪 ✅" if ok else "TTS 服务启动超时（仍会继续，但语音可能走冷启动回退）")

    # ② 常驻数字人服务（热 LivePortrait）
    if C.USE_AVATAR_SERVICE:
        if _is_ready(C.AVATAR_SERVICE_URL + "/ready"):
            _log(f"数字人服务已在运行：{C.AVATAR_SERVICE_URL}（热推理就绪）")
        else:
            _spawn([C.EMBEDDED_PY, os.path.join(HERE, "avatar_service.py"),
                    "--host", "127.0.0.1", "--port", str(C.AVATAR_SERVICE_PORT), "--eager"],
                   os.path.join(C.DIR_TEST, "avatar_service.log"), "数字人服务")
            ok = _probe(C.AVATAR_SERVICE_URL + "/ready", timeout=240)
            _log("数字人服务就绪 ✅" if ok else "数字人服务启动超时（视频功能将回退冷启动，或不可用）")

    # ③ Web 应用（系统 python）
    web_cmd = [sys.executable, os.path.join(HERE, "app.py")]
    web = _spawn(web_cmd, os.path.join(C.DIR_TEST, "web.log"), "Web 应用")
    if web is None:
        _log("[!] Web 启动失败，退出。")
        cleanup()
        return
    web_ready = _probe("http://127.0.0.1:" + str(C.__dict__.get("PORT", 7860)) + "/",
                      timeout=30)
    # app.py 的 PORT 在模块内定义，这里直接读环境变量兜底
    port = os.environ.get("MYME_PORT", "7860")
    if not web_ready:
        web_ready = _probe(f"http://127.0.0.1:{port}/", timeout=30)
    _log("Web 就绪 ✅" if web_ready else "Web 就绪探测超时（请查看 web.log）")

    _log("=" * 56)
    _log(f"炎冰数字分身已启动：http://127.0.0.1:{port}")
    _log("  · 炎冰声音(TTS)：      " + C.TTS_SERVICE_URL)
    _log("  · 视频数字人(LivePortrait)：" + C.AVATAR_SERVICE_URL)
    _log("按 Ctrl+C 退出（会自动收尾所有后台进程）。")
    _log("=" * 56)

    # 保持前台存活，直到 Web 退出或用户中断
    try:
        while True:
            rc = web.poll()
            if rc is not None:
                _log(f"Web 进程已退出（返回码 {rc}），收尾并退出。")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        _log("收到 Ctrl+C，收尾…")
    finally:
        cleanup()


def ensure_dirs_log():
    try:
        os.makedirs(C.DIR_TEST, exist_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
