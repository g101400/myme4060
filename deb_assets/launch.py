# -*- coding: utf-8 -*-
"""myme 播放版启动器（纯 python3，不依赖任何外部命令）。

================================================================================
为什么把启动逻辑从 sh 搬进 python3（v2.9.3 定案，2026-10-16 实测复盘）
================================================================================
旧启动器是纯 sh，用 curl / wget 探测"服务起没起来"、用 setsid 命令后台脱离。
本地仿真复现出**三类静默失败**，共同表现就是用户说的「装了但打不开 / 点了没反应」：

  ① 机器配了系统 HTTP 代理（UOS 专业版内网环境极常见）：
     `curl -s -o /dev/null http://127.0.0.1:7862/manifest.json` 会**走代理**，
     代理回 502 —— 而 curl 对"有 HTTP 应答"一律返回 0。启动器据此判定
     「服务已在运行」，于是**根本不启动服务**，直接打开死端口 → 白屏。
     （实测：http_code=502 且 rc=0；加 --noproxy '*' 才是 000/rc=7）
  ② 精简系统里 curl 与 wget 都缺：服务其实已经起来（实测探针 200），
     探测循环仍必然返回 1 → 假失败，还白等 10~22 秒。
  ③ 没有 setsid 命令（busybox / 精简 rootfs）：`setsid: command not found`，
     后台服务根本没起，而诊断信息只 echo 到 stdout —— 桌面点图标时没有终端，
     用户什么都看不到。

三类都是"看不到原因的失败"。现在统一改成：
  · **探测**走 python 自己的 urllib + 显式空代理（ProxyHandler({})），
    并只认 serve.py 的 /__myme_ping__ magic token；
  · **后台脱离**用 subprocess(start_new_session=True)，内部就是 os.setsid()，
    不需要外部 setsid 命令；
  · **失败绝不静默**：写日志 + 桌面通知（notify-send / zenity / kdialog）+
    生成一张中文诊断页并用浏览器打开，把"下一步该敲什么命令"直接摆在用户面前；
  · 把 127.0.0.1 / localhost / ::1 强制写进 no_proxy，同时惠及子进程与浏览器
    （否则浏览器也会把本机地址丢给代理 → 白屏）。
================================================================================
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser

VERSION = "@VER@"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MYME_PLAYER_ROOT") or HERE
PORT0 = 7862
PING_PATH = "/__myme_ping__"
PING_TOKEN = "myme-player-ok"
TMP = tempfile.gettempdir()
LOG = os.path.join(TMP, "myme-player.log")
PIDF = os.path.join(TMP, "myme-player.pid")
PORTF = os.path.join(TMP, "myme-player.port")
STATUSF = os.path.join(TMP, "myme-player.status")
ERRPAGE = os.path.join(TMP, "myme-player-error.html")


# ------------------------------------------------------------------ 日志
def _say(msg):
    line = "[myme-player %s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass


# ------------------------------------------------------------------ 代理
def strip_proxy():
    """把 loopback 排除在代理之外 —— 进程自身、子进程、浏览器都吃到这份环境。"""
    for k in ("no_proxy", "NO_PROXY"):
        parts = [p.strip() for p in os.environ.get(k, "").split(",") if p.strip()]
        for h in ("127.0.0.1", "localhost", "::1"):
            if h not in parts:
                parts.append(h)
        os.environ[k] = ",".join(parts)


def _opener():
    """显式空代理的 opener。

    ★ 这是本版最关键的一行：绝不允许探测本机地址时走代理。
      旧版用 curl，代理回 502 也被当成"成功"，于是跳过起服务 → 白屏。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port, timeout=0.25):
    """本机服务是否**真的**就绪：只认 serve.py 的 magic token。

    超时刻意短：启动时要先快速扫一遍候选端口，逐个 1 秒会把启动拖到十几秒
    （Windows / 有防火墙丢包的机器上尤其明显）。等就绪循环里再放宽（见 wait_ready）。
    """
    url = "http://127.0.0.1:%d%s" % (port, PING_PATH)
    try:
        with _opener().open(url, timeout=timeout) as r:
            body = r.read(64)
        return PING_TOKEN.encode("ascii") in body
    except Exception:
        return False


# ------------------------------------------------------------------ 端口 / 进程
def reuse_running():
    """已有本服务在跑就复用它（避免多开）。

    ★ 探针只认 serve.py 的 magic token，且显式不走代理 ——
      旧版用 curl 探测，代理回 502 也算"成功"，于是误判"已在运行"、
      直接跳过起服务去打一个死端口（白屏第一根因）。
    """
    cands = []
    try:
        with open(PORTF) as f:
            v = int(f.read().strip())
        cands.append(v)
    except Exception:
        pass
    cands += [p for p in range(PORT0, PORT0 + 6) if p not in cands]
    for p in cands:
        if probe(p):
            return p
    return None


def pick_port(start=PORT0, tries=12):
    """由启动器选定空闲端口，再显式传给 serve.py —— 探针目标与真实端口不会再错位。"""
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    return start


def kill_stale():
    try:
        pid = int(open(PIDF).read().strip())
    except Exception:
        return
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmd = f.read().decode("utf-8", "ignore")
        if "serve.py" in cmd and "myme" in cmd:
            os.kill(pid, 15)
            _say("已停止旧播放服务 pid=%d" % pid)
            time.sleep(0.3)
    except Exception:
        pass


def spawn(port):
    """后台起服务。

    start_new_session=True 内部调用 os.setsid()，让服务成为**新会话领头进程**：
      · 不依赖系统里有没有 setsid 命令（busybox 精简环境没有）；
      · 桌面环境在启动器退出时不会连带把整个进程组杀掉（旧版白屏根因之一）。
    """
    env = dict(os.environ)
    args = [sys.executable, os.path.join(HERE, "serve.py"), str(port), ROOT]
    try:
        logf = open(LOG, "ab", buffering=0)
    except Exception:
        logf = None
    if logf is not None:
        try:
            logf.write(("\n[%s] 启动 serve.py port=%d root=%s\n"
                        % (time.strftime("%H:%M:%S"), port, ROOT)).encode("utf-8"))
        except Exception:
            pass
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=(logf or subprocess.DEVNULL),
                         stderr=(logf or subprocess.DEVNULL),
                         cwd=ROOT, env=env, start_new_session=True, close_fds=True)
    try:
        with open(PIDF, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    return p


def wait_ready(port, seconds=25.0, proc=None):
    """等探针命中。子进程已退出就别再干等 —— 立刻转入诊断，省用户 20 多秒。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        if probe(port, 1.0):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.25)
    return probe(port, 1.0)


def file_url(path):
    """本地文件转 file:// URL（Linux 下 /tmp/x → file:///tmp/x）。"""
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + p


# ------------------------------------------------------------------ 打开浏览器
def open_url(url):
    """依次尝试可靠的打开方式，每一步都记日志（无终端时靠日志排障）。

    MYME_PLAYER_NO_OPEN=1 时只记日志不真开 —— 供无头环境 / 自测使用，
    避免把浏览器窗口弹到跑测试的机器上。
    """
    if os.environ.get("MYME_PLAYER_NO_OPEN") == "1":
        _say("MYME_PLAYER_NO_OPEN=1，跳过打开浏览器：%s" % url)
        return True
    for cmd in (["xdg-open"], ["dde-open"], ["gio", "open"]):
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            subprocess.Popen(cmd + [url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            _say("已用 %s 打开：%s" % (cmd[0], url))
            return True
        except Exception as e:
            _say("%s 打开失败：%r" % (cmd[0], e))
    try:
        if webbrowser.open(url):
            _say("已用系统默认浏览器打开：%s" % url)
            return True
    except Exception as e:
        _say("webbrowser.open 失败：%r" % (e,))
    for cmd in (["kde-open"], ["gnome-open"], ["sensible-browser"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.Popen(cmd + [url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            _say("已用 %s 打开：%s" % (cmd[0], url))
            return True
        except Exception as e:
            _say("%s 打开失败：%r" % (cmd[0], e))
    return False


def notify(title, msg):
    for cmd in (["notify-send", "-u", "critical", title, msg],
                ["zenity", "--error", "--title", title, "--text", msg],
                ["kdialog", "--error", msg],
                ["xmessage", "-center", msg]):
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            _say("已弹提示（%s）" % cmd[0])
            return True
        except Exception:
            continue
    return False


# ------------------------------------------------------------------ 失败诊断页
ERR_HTML = u"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>myme 播放版 · 启动失败诊断</title>
<style>
:root{--acc:#1d4ed8;--acc2:#3b82f6;--bg:#f5f7fb;--panel:#fff;--line:#e3e8f0;
 --text:#1f2937;--mut:#6b7280;--err:#b91c1c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font-family:"PingFang SC","Microsoft YaHei","Source Han Sans SC",system-ui,sans-serif;
 line-height:1.7;font-size:15px}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 64px}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:18px}
.logo{width:40px;height:40px;border-radius:11px;background:linear-gradient(135deg,var(--acc),var(--acc2));
 color:#fff;display:grid;place-items:center;font-weight:700;font-size:14px;
 box-shadow:0 4px 14px rgba(29,78,216,.3)}
h1{margin:0;font-size:21px;letter-spacing:.2px}
.sub{font-size:12.5px;color:var(--mut)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;
 margin:14px 0;box-shadow:0 1px 2px rgba(16,24,40,.04),0 8px 24px rgba(16,24,40,.05)}
h2{margin:0 0 10px;font-size:15px;color:var(--err)}
ul{margin:0;padding-left:22px}
li{margin:4px 0}
code,kbd{background:#eef2f8;border:1px solid var(--line);border-radius:6px;padding:2px 7px;
 font-family:"JetBrains Mono",Consolas,Menlo,monospace;font-size:13px}
pre{background:#0f172a;color:#e5edff;border-radius:10px;padding:12px 14px;overflow:auto;
 font-family:"JetBrains Mono",Consolas,Menlo,monospace;font-size:12.5px;line-height:1.6}
.mono{font-family:"JetBrains Mono",Consolas,Menlo,monospace;font-size:13px;
 background:#eef2f8;border:1px solid var(--line);border-radius:8px;padding:8px 10px;word-break:break-all}
.tag{display:inline-block;font-size:12px;color:var(--mut);border:1px solid var(--line);
 border-radius:999px;padding:1px 9px;margin-left:6px}
</style></head>
<body><div class="wrap">
  <div class="brand"><div class="logo">myme</div>
    <div><h1>myme 播放版 · 启动失败诊断</h1>
      <div class="sub">版本 @VER@ · 这份页面由启动器自动生成，不是报错弹窗，请照下面的步骤处理</div></div></div>

  <div class="card">
    <h2>检测到的问题</h2>
    <ul>@REASONS@</ul>
  </div>

  <div class="card">
    <h2>最快的解法：手动起一次（复制到终端执行）</h2>
    <div class="mono">@CMD@</div>
    <p class="sub" style="margin:8px 0 0">执行后不要关终端，在浏览器打开
      <code>http://127.0.0.1:@PORT@/player.html</code> 即可。要停止服务按 Ctrl+C。</p>
  </div>

  <div class="card">
    <h2>进阶排查</h2>
    <ul>
      <li>播放包目录：<code>@ROOT@</code>（应含 <code>player.html</code> 与 <code>manifest.json</code>）</li>
      <li>启动日志：<code>@LOG@</code>　<span class="tag">最近 40 行见下</span></li>
      <li>若机器配了 HTTP 代理：请确认 <code>127.0.0.1</code> / <code>localhost</code> 在代理例外里。
        浏览器把本机地址丢给代理就会白屏；启动器已强制写入 <code>no_proxy</code>，
        但桌面环境自带的代理设置仍需放行本机地址。</li>
      <li>端口被占：杀掉旧进程后重试　<code>pkill -f serve.py</code>（或重启一次即可）</li>
    </ul>
  </div>

  <div class="card">
    <h2>日志尾部</h2>
    <pre>@TAIL@</pre>
  </div>
</div></body></html>
"""


def diagnose(port, p=None):
    """把"为什么没起来"整理成人能看懂的原因清单。"""
    reasons = []
    if not shutil.which("python3") and not shutil.which("python"):
        reasons.append("系统里找不到 <code>python3</code>：播放服务就是一段 python 脚本，"
                       "请先装 <code>python3</code>（<code>sudo apt install python3</code>）。")
    if not os.path.isdir(ROOT):
        reasons.append("播放包目录不存在：<code>%s</code>（可能没装上或已被卸载）。" % ROOT)
    else:
        for f in ("player.html", "manifest.json"):
            if not os.path.isfile(os.path.join(ROOT, f)):
                reasons.append("播放包目录里缺 <code>%s</code>：安装包可能没拷全，建议重装。" % f)
    if p is not None and p.poll() is not None:
        reasons.append("后台服务进程立刻退出（退出码 %s）：多半是缺 python3 标准库或目录不可读，"
                       "详见下面的日志尾部。" % p.returncode)
    if not probe(port):
        reasons.append("127.0.0.1:%d 上探测不到本播放服务："
                       "端口可能被别的程序占着，或服务压根没起来。" % port)
    if os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or \
       os.environ.get("all_proxy") or os.environ.get("ALL_PROXY"):
        reasons.append("检测到系统设置了 HTTP 代理（<code>http_proxy</code>）。"
                       "本机地址必须走代理例外，否则浏览器打开会白屏。")
    if not reasons:
        reasons.append("服务在预期时间内没有响应探针，原因未能自动判定；请看下面的日志尾部。")
    return reasons


def write_error_page(reasons, port):
    tail = ""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        tail = "\n".join(lines[-40:]) or "(日志为空)"
    except Exception as e:
        tail = "(读不到日志：%r)" % (e,)
    html = ERR_HTML
    html = html.replace("@REASONS@", "".join("<li>%s</li>" % r for r in reasons))
    html = html.replace("@CMD@", "python3 %s %d %s" % (os.path.join(HERE, "serve.py"), port, ROOT))
    html = html.replace("@LOG@", LOG)
    html = html.replace("@ROOT@", ROOT)
    html = html.replace("@PORT@", str(port))
    html = html.replace("@VER@", VERSION)
    html = html.replace("@TAIL@", tail.replace("&", "&amp;").replace("<", "&lt;"))
    try:
        with open(ERRPAGE, "w", encoding="utf-8") as f:
            f.write(html)
        return ERRPAGE
    except Exception as e:
        _say("写诊断页失败：%r" % (e,))
        return None


def write_status(state, port, note=""):
    try:
        with open(STATUSF, "w", encoding="utf-8") as f:
            f.write("state=%s\nport=%d\nroot=%s\nlog=%s\ntime=%s\nnote=%s\n"
                    % (state, port, ROOT, LOG, time.strftime("%Y-%m-%d %H:%M:%S"), note))
    except Exception:
        pass


# ------------------------------------------------------------------ 主流程
def main():
    strip_proxy()
    os.makedirs(TMP, exist_ok=True)
    _say("=" * 72)
    _say("myme 播放版启动器 v%s　root=%s　python=%s" % (VERSION, ROOT, sys.version.split()[0]))
    _say("no_proxy=%s" % os.environ.get("no_proxy", ""))

    # 1) 已经有本服务在跑？复用
    hit = reuse_running()
    if hit is not None:
        _say("复用已在运行的播放服务：端口 %d" % hit)
        write_status("reuse", hit)
        open_url("http://127.0.0.1:%d/player.html" % hit)
        return 0

    # 2) 选端口 + 清掉可能残留的旧服务
    port = pick_port(PORT0)
    kill_stale()

    # 3) 后台起服务（新会话，脱离桌面进程组）
    try:
        proc = spawn(port)
    except Exception as e:
        reasons = ["无法启动后台服务进程：<code>%s</code>" % e]
        page = write_error_page(reasons, port)
        notify("myme 播放版", "启动失败：" + str(e))
        write_status("fail", port, str(e))
        if page:
            open_url(file_url(page))
        return 2
    _say("已请求启动：pid=%s port=%d" % (proc.pid, port))

    # 4) 等就绪
    if not wait_ready(port, 25.0, proc):
        reasons = diagnose(port, proc)
        page = write_error_page(reasons, port)
        plain = "；".join(r.replace("<code>", "").replace("</code>", "")
                          .replace("<li>", "").replace("</li>", "") for r in reasons)
        notify("myme 播放版 启动失败", plain[:220] + "（诊断页已打开，日志：" + LOG + "）")
        write_status("fail", port, plain[:200])
        _say("❌ 启动失败：" + plain)
        if page:
            open_url(file_url(page))
        return 2

    # 5) 打开播放器
    url = "http://127.0.0.1:%d/player.html" % port
    _say("✅ 服务就绪：%s" % url)
    write_status("ok", port)
    if not open_url(url):
        _say("⚠ 没能自动打开浏览器，请手动访问：%s" % url)
        notify("myme 播放版", "服务已就绪，请在浏览器打开：" + url)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        _say("启动器自身异常：%r" % (e,))
        _say(traceback.format_exc())
        sys.exit(3)
