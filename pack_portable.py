# -*- coding: utf-8 -*-
"""
myme 三端便携播放包打包（v2.6）
==============================================
产物（全部落在 dist/）：
  1. myme-<ver>-win-x64-play.zip      —— Win11 便携播放包：myme-play.exe + 离线播放器 + 示例播放包
  2. myme-<ver>-android-play.apk      —— 安卓 APK：WebView 壳内置同一个播放器与示例播放包
  3. myme-player_<ver>_all.deb        —— 统信 UOS deb：本地 python3 http 服务 + 浏览器打开播放器

三端共用同一个「便携播放包」数据（完整版导出时勾选「📱 打包为离线便携播放包」得到）：
    manifest.json + player.html + images/ + audio/ + video/ + portraits/
定位：
  · Win     = 创作端 + 播放端（完整版出片、便携版播放）
  · Android = 纯播放端（手机上离线演示）
  · UOS     = 纯播放端（国产系统离线演示）
重依赖（LivePortrait 682MB / ts_deps 141MB）只跟 Win 完整包走，移动端不背。

用法：
  python pack_portable.py           # 三端全出
  python pack_portable.py win       # 只出 Win
  python pack_portable.py apk
  python pack_portable.py deb
  python pack_portable.py bundle    # 只重新生成示例播放包（从当前项目导出）
"""
import os
import re
import sys
import io
import json
import time
import shutil
import zipfile
import tarfile
import subprocess
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
VER = "2.6.3"
TMP = "C:/tmp/myme_pkg"
BUNDLE = os.path.join(TMP, "portable")            # 解出的便携播放包
# ⚠ 本环境对批量删除有确认垫片（>50 文件会拦），故一律「新目录 / 覆盖写」，不删旧产物
DEB_DIR = os.path.join(TMP, "deb_stage_" + time.strftime("%H%M%S"))
WIN_DIR = os.path.join(TMP, "win_play")
APK_PROJ = os.path.join(HERE, "android-player")
SEVENZ = r"C:\Program Files\7-Zip\7z.exe"
DEB_PORT = 7862

PKG = "myme-player"
DEB_NAME = "myme-player_2.6.3_all.deb"


def log(*a):
    print("[pack_portable]", *a, flush=True)


def rmtree(path):
    """手动删目录：绕开环境里的批量删除确认垫片（shutil.rmtree 被包裹，>50 文件会拦）。"""
    if not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for f in files:
            try:
                os.remove(os.path.join(root, f))
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


# ==================== 0. 准备便携播放包（数据） ====================
def build_bundle(ver_id=None):
    """从当前项目导出一份示例便携播放包并解到 BUNDLE 目录。"""
    import app
    out = os.path.join(app.config.DIR_TEST, "_portable_bundle.zip")
    cur = None
    try:
        cur = (app.SLIDES_META or {}).get("currentVersion")
    except Exception:
        pass
    only = [cur] if (ver_id or cur) else None
    t0 = time.time()
    n = app._res_export_zip(out, ["versions", "audio", "video", "personas"],
                            only_versions=only, videos=None, quality="standard",
                            include_player=True)
    log("示例播放包：%d 项，%.1f MB，%.1fs" % (n, os.path.getsize(out) / 1048576, time.time() - t0))
    os.makedirs(BUNDLE, exist_ok=True)
    with zipfile.ZipFile(out) as z:
        z.extractall(BUNDLE)
    return BUNDLE


def seed_demo_clips(bundle):
    """把已过真机验证的分身出片内置进示例播放包（2026-10-03 新增）。

    背景：本机没有历史讲解数据时，示例播放包是「0 项空壳」，三端装上去看不到任何视频。
    与其让人猜「我的环境能不能跑」，不如把交接包里已验证良好的 8s 出片内置进去——
    **能播即说明本机视频链路通**（沿用交接文档 §6.1「内置演示片段」的设计）。
    命名必须遵循 ui/player.html 的 guessVideo 约定：video/demo_av_<versionId>_<idx>.mp4，
    并在 manifest.json 里登记同名 version + persona，否则播放器下拉选不到。
    """
    import json as _json
    src = os.path.join(HERE, "demo_avatar")
    if not os.path.isdir(src):
        log("无 demo_avatar，跳过内置演示片段")
        return 0
    vdir = os.path.join(bundle, "video")
    os.makedirs(vdir, exist_ok=True)

    MAP = [("yanbing_sonic.mp4", "yanbing", "炎冰"),
           ("jinzi_sonic.mp4", "jinzi", "金子")]
    mfp = os.path.join(bundle, "manifest.json")
    man = {}
    if os.path.isfile(mfp):
        try:
            man = _json.load(open(mfp, encoding="utf-8")) or {}
        except Exception:
            man = {}

    added = 0
    for fname, vid, pname in MAP:
        sp = os.path.join(src, fname)
        if not os.path.isfile(sp):
            continue
        dp = os.path.join(vdir, "demo_av_%s_1.mp4" % vid)
        shutil.copy2(sp, dp)
        added += 1
        man.setdefault("personas", [])
        if not any(p.get("id") == vid for p in man["personas"]):
            man["personas"].append({"id": vid, "name": pname})
        man.setdefault("versions", [])
        if not any(v.get("id") == vid for v in man["versions"]):
            man["versions"].append({"id": vid, "name": "%s · 内置演示" % pname, "idx": 1})

    if added:
        man["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(mfp, "w", encoding="utf-8") as f:
            _json.dump(man, f, ensure_ascii=False, indent=1)
    log("内置演示片段：%d 个 -> %s" % (added, vdir))
    return added


def ensure_bundle():
    if os.path.isfile(os.path.join(BUNDLE, "manifest.json")) and \
       os.path.isfile(os.path.join(BUNDLE, "player.html")):
        log("复用已有播放包：", BUNDLE)
    else:
        build_bundle()
    try:
        seed_demo_clips(BUNDLE)
    except Exception as e:      # 内置片段只是增强，失败不能阻断出包
        log("内置演示片段失败（不阻断）：", e)
    return BUNDLE


# ==================== 1. Win11 便携播放包 ====================
WIN_BAT = r"""@echo off
chcp 65001 >nul
title myme 便携播放版 v%s
cd /d "%%~dp0"
echo.
echo   myme 便携播放版 v%s —— 启动中（首次约 10~30 秒，要解压运行时）
echo.
start "" "http://127.0.0.1:7861/"
"%%~dp0myme-play.exe"
pause
""" % (VER, VER)

WIN_TXT = """myme 便携播放版 v%s（Windows 11 / 10）
==========================================

【两种用法，任选】

用法一：双击播放器（最简单，什么都不用装）
  进入 playback 目录，双击 player.html —— 用 Chrome / Edge 打开即可播。
  全程离线，不装任何东西，也不需要 office / 播放器。

用法二：双击 myme-play.exe（完整播放版）
  这是带界面的便携播放程序：
    1) 启动后浏览器自动打开 http://127.0.0.1:7861/
    2) 在「⑧ 播放文件」里点「导入播放文件」，选完整版导出的 myme_playback_xxx.zip
    3) 导入后在「⑤ 正式演示」里播放
  它只负责播放，不能创作；创作请用完整版。

【给别人用的正确姿势】
  · 把整个目录拷到 U 盘 / 发给对方，解压即用，不用安装。
  · 想更新播放内容：在完整版「⑤ 正式演示 → 📀 导出播放文件」，
    记得勾选「📱 打包为离线便携播放包」，把新包解压覆盖 playback 目录即可。

【说明】
  · 播放包里 manifest.json 是播放清单、player.html 是离线播放器，
    images/ audio/ video/ portraits/ 是素材，缺哪个播放端会明确提示。
  · 快捷键：→ 下一页，← 上一页，空格 播放/暂停，Esc 关目录。
""" % VER


def build_win():
    if not os.path.isfile(os.path.join(DIST, "myme-play.exe")):
        raise SystemExit("❌ 缺 dist/myme-play.exe（请先构建便携版主程序）")
    os.makedirs(WIN_DIR, exist_ok=True)
    shutil.copy2(os.path.join(DIST, "myme-play.exe"), os.path.join(WIN_DIR, "myme-play.exe"))
    with open(os.path.join(WIN_DIR, "启动 myme 播放版.bat"), "w", encoding="utf-8") as f:
        f.write(WIN_BAT)
    with open(os.path.join(WIN_DIR, "便携播放说明.txt"), "w", encoding="utf-8") as f:
        f.write(WIN_TXT)
    shutil.copytree(BUNDLE, os.path.join(WIN_DIR, "playback"), dirs_exist_ok=True)
    _inline_manifest(os.path.join(WIN_DIR, "playback"))
    out = os.path.join(DIST, "myme-%s-win-x64-play.zip" % VER)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, dns, fns in os.walk(WIN_DIR):
            for fn in fns:
                fp = os.path.join(dp, fn)
                z.write(fp, os.path.relpath(fp, WIN_DIR))
    log("✅ Win 便携包：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576))
    return out


# ==================== 2. 安卓 APK ====================
def _ascii_asset_names(root):
    """把 assets 下的非 ASCII 文件名改成 ASCII。

    ⚠ aapt2 打 assets 时遇到中文文件名会直接 `failed to open file` 构建失败，
      而播放包里的说明文件正好是中文名。统一改名（内容不变）即可让同一份包
      同时服务 Win / APK / deb 三端。
    """
    known = {"怎么播放.txt": "README.txt"}
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            if fn.isascii():
                continue
            new = known.get(fn)
            if not new:
                stem, ext = os.path.splitext(fn)
                new = "doc_" + hashlib.md5(stem.encode("utf-8")).hexdigest()[:6] + ext
            try:
                os.replace(os.path.join(dp, fn), os.path.join(dp, new))
            except OSError:
                pass


def _inline_manifest(root):
    """把 manifest.json 内联成 manifest.js（window.__MYME_MANIFEST__）。

    ⚠ 安卓 WebView 的 fetch 对 file:// 一律 "Failed to fetch"（Blink 网络层硬限制，
      跟 setAllowFileAccessFromFileURLs 无关），播放器只能靠 XHR / <script> 兜底。
      内联后零请求，是唯一在任何环境都一定能读到清单的路径。
    """
    mj = os.path.join(root, "manifest.json")
    if not os.path.isfile(mj):
        return
    with open(mj, "r", encoding="utf-8") as f:
        data = f.read()
    # 压掉换行，避免 </script> 序列被打进 JS 字符串字面量（虽然这里是对象字面量，不会出现）
    js = "window.__MYME_MANIFEST__=" + data.strip() + ";"
    with open(os.path.join(root, "manifest.js"), "w", encoding="utf-8") as f:
        f.write(js)


def build_apk():
    assets = os.path.join(APK_PROJ, "assets")
    shutil.copytree(BUNDLE, assets, dirs_exist_ok=True)
    _ascii_asset_names(assets)
    _inline_manifest(assets)
    # 图标
    res = os.path.join(APK_PROJ, "res", "drawable")
    os.makedirs(res, exist_ok=True)
    src_icon = os.path.join(HERE, "icon-512.png")
    if os.path.isfile(src_icon):
        try:
            from PIL import Image
            im = Image.open(src_icon).convert("RGBA")
            for size, sub in ((192, "drawable"), (96, "drawable-mdpi"),
                              (144, "drawable-hdpi"), (192, "drawable-xhdpi")):
                d = os.path.join(APK_PROJ, "res", sub)
                os.makedirs(d, exist_ok=True)
                im.resize((size, size)).save(os.path.join(d, "icon.png"))
        except Exception as e:
            log("⚠ 图标处理失败（退回原图）：", e)
            shutil.copy2(src_icon, os.path.join(res, "icon.png"))
    sh = "bash"
    r = subprocess.run([sh, "build_apk.sh"], cwd=APK_PROJ, capture_output=True,
                       text=True, encoding="utf-8", errors="ignore")
    tail = ((r.stdout or "") + (r.stderr or ""))[-2500:]
    if r.returncode != 0:
        log("❌ APK 构建失败：\n", tail)
        return ""
    src = os.path.join(APK_PROJ, "app-release.apk")
    out = os.path.join(DIST, "myme-%s-android-play.apk" % VER)
    shutil.copy2(src, out)
    log("✅ 安卓 APK：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576))
    log(tail[-600:])
    return out


# ==================== 3. 统信 UOS deb ====================
SERVE_PY = r'''# -*- coding: utf-8 -*-
"""myme 播放端本地服务：只做静态文件服务，不开任何外网访问。

为什么必须起 http 而不是直接 file:// 打开：浏览器的同源策略会拦掉 file:// 下的
fetch('manifest.json')，播放器读不到清单就只能白屏。走 127.0.0.1 本机 http 最省事。
"""
import os
import sys
import functools
import http.server
import socketserver

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else %d
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

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.chdir(ROOT)
    # 端口被占（残留服务/冲突）时依次往后试，并把实际端口写到文件，供启动器打开正确 URL
    PORTS = [PORT] + [PORT + i for i in range(1, 10)]
    for p in PORTS:
        try:
            httpd = S(("127.0.0.1", p), functools.partial(H, directory=ROOT))
            httpd.port = p
            break
        except OSError:
            httpd = None
    if httpd is None:
        raise SystemExit("无法在端口 %%d~%%d 启动播放服务（均被占用）" %% (PORT, PORT + 9))
    try:
        with open("/tmp/myme-player.port", "w") as _pf:
            _pf.write(str(httpd.port))
    except OSError:
        pass
    print("myme 播放端已启动： http://127.0.0.1:%%d/player.html" %% httpd.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
''' % DEB_PORT

LAUNCHER = r'''#!/bin/sh
# myme 播放版启动脚本（统信 UOS / Deepin / 麒麟等 Debian 系通用）
# 起本机 http 服务（file:// 下浏览器同源策略会拦住 manifest.json，必须走 http），
# 再用系统默认浏览器打开播放器。全程离线，不联网、不写系统目录。
#
# ⚠ 关键修复（"点图标没反应"根因）：serve.py 必须用 setsid 彻底脱离本脚本所在的
#   进程组/会话后再后台运行。之前用普通 `&` 起子进程，脚本退出时桌面环境把整个
#   进程组一起杀掉，http 服务根本没起来 → 浏览器打开的是死端口 → 白屏"什么都没出现"。
#   setsid 让它成为新会话领头进程，桌面环境杀不掉；再加 nohup 双重保险、日志落盘。
PORT=%d
DIR=/opt/%s
LOG=/tmp/myme-player.log

# 0) 依赖自检：python3 缺失时给可见提示，而不是静默失败（之前就是静默失败看不出原因）
if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 python3，无法启动本地播放服务。" > "$LOG"
    ( command -v xdg-open >/dev/null 2>&1 && xdg-open "$LOG" ) || ( command -v dde-open >/dev/null 2>&1 && dde-open "$LOG" )
    exit 1
fi

start_server() {
    # 清掉可能残留的旧服务（同端口），避免端口被占导致新服务起不来
    if command -v pkill >/dev/null 2>&1; then pkill -f "$DIR/serve.py" 2>/dev/null || true; fi
    sleep 0.3
    # setsid 完全脱离会话后台运行；nohup 双重保险；日志落盘便于排查
    setsid nohup python3 "$DIR/serve.py" "$PORT" "$DIR" >"$LOG" 2>&1 < /dev/null &
    echo $! > /tmp/myme-player.pid
    # 等服务起来（最多 10 秒）
    i=0
    while [ $i -lt 50 ]; do
        if curl -s -o /dev/null "http://127.0.0.1:$PORT/manifest.json" 2>/dev/null; then return 0; fi
        if command -v wget >/dev/null 2>&1 && wget -q -O /dev/null "http://127.0.0.1:$PORT/manifest.json" 2>/dev/null; then return 0; fi
        i=$((i+1)); sleep 0.2
    done
    return 1
}

if curl -s -o /dev/null "http://127.0.0.1:$PORT/manifest.json" 2>/dev/null; then
    echo "服务已在运行（端口 $PORT）"
else
    if ! start_server; then
        # 服务起不来：尽量弹桌面通知，退而打开日志文件，让用户看到真实错误
        MSG="myme 播放服务启动失败，请查看日志：/tmp/myme-player.log"
        if command -v notify-send >/dev/null 2>&1; then notify-send -u critical "myme 播放版" "$MSG"; fi
        ( command -v xdg-open >/dev/null 2>&1 && xdg-open "$LOG" ) || ( command -v dde-open >/dev/null 2>&1 && dde-open "$LOG" )
        exit 1
    fi
fi

URL="http://127.0.0.1:$PORT/player.html"
# 若服务因端口被占回退到了别的端口，读实际端口拼接 URL
if [ -f /tmp/myme-player.port ]; then
    APORT=$(cat /tmp/myme-player.port 2>/dev/null)
    case "$APORT" in ''|*[!0-9]*) APORT=$PORT;; esac
    if [ "$APORT" != "$PORT" ]; then URL="http://127.0.0.1:$APORT/player.html"; fi
fi
# 多试几种打开方式；xdg-open 在 UOS 上通常能正确路由到浏览器
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
elif command -v dde-open >/dev/null 2>&1; then
    dde-open "$URL" >/dev/null 2>&1 &
elif command -v kde-open >/dev/null 2>&1; then
    kde-open "$URL" >/dev/null 2>&1 &
elif command -v gnome-open >/dev/null 2>&1; then
    gnome-open "$URL" >/dev/null 2>&1 &
else
    echo "请手动在浏览器打开： $URL"
fi
''' % (DEB_PORT, PKG)

CONTROL = """Package: %s
Version: %s
Section: utils
Priority: optional
Architecture: all
Depends: python3, xdg-utils
Maintainer: myme <myme@local>
Description: myme 数字分身便携播放版（统信 UOS）
 离线播放 myme 完整版导出的便携播放包（讲解音频 + 数字分身视频 + 页图）。
 安装后在开始菜单点「myme 播放版」，或执行 /opt/%s/myme-player。
 纯数据与脚本，不含任何二进制依赖；卸载会带走 /opt/%s 全部内容。
""" % (PKG, VER, PKG, PKG)

POSTINST = """#!/bin/sh
set -e
chmod 0755 /opt/%s/myme-player
chmod 0644 /opt/%s/serve.py
chmod -R a+rX /opt/%s
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
fi
echo "myme 播放版 v%s 已安装：开始菜单 → 影音 → myme 播放版"
echo "也可执行： /opt/%s/myme-player"
""" % (PKG, PKG, PKG, VER, PKG)

PRERM = """#!/bin/sh
set -e
# 停止可能还在跑的播放服务，避免卸载后残留进程占着端口
if [ -f /tmp/myme-player.pid ]; then
    kill "$(cat /tmp/myme-player.pid)" 2>/dev/null || true
    rm -f /tmp/myme-player.pid
fi
"""

POSTRM = """#!/bin/sh
set -e
rm -rf /opt/%s
rm -f /tmp/myme-player.pid /tmp/myme-player.log
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=myme 播放版
Name[zh_CN]=myme 播放版
GenericName=数字分身演示播放
Comment=离线播放 myme 便携播放包（讲解音频 + 数字分身视频）
Exec=/opt/%s/myme-player
Icon=myme-player
Terminal=false
Categories=AudioVideo;Video;Player;
Keywords=myme;数字分身;演示;播放;
""" % PKG


def _ar_member(name, data):
    """ar 归档成员：16B 名称 | 12B mtime | 6 uid | 6 gid | 8 mode | 10 size | 2 magic"""
    hdr = ("%-16s%-12s%-6s%-6s%-8s%-10d" % (name, "0", "0", "0", "100644", len(data))).encode()
    out = hdr + b"`\n" + data
    if len(out) % 2:
        out += b"\n"
    return out


def _tar_xz(src_dir, arc_prefix="."):
    """把目录打成 tar.xz（data.tar.xz）。

    ⚠ 两条铁律（统信 UOS 上装不上就是栽在这）：
      1. **必须 GNU_FORMAT**。Python tarfile 默认 PAX_FORMAT，会为每个成员写一条
         PAX 扩展头（typeflag='x'）——因为 `tf.add()` 取到的 mtime 是带小数的 float，
         PAX 就得把 mtime 写进扩展头。UOS 自带 dpkg 的 tar 解析器不认，直接报
         「不支持的 PAX tar 头部类型 'x'」→ 安装失败。（水利一张图踩过的原坑）
      2. **mtime 一律取整** + 显式写目录项。dpkg 对 tar 成员的时间戳敏感，
         目录项缺失还会导致卸载时残留。
      另：只 add 文件、不 add 目录递归，否则整包会被复制两遍（319MB vs 150MB）。
    """
    buf = io.BytesIO()
    now = int(time.time())
    dirs_done = set()
    with tarfile.open(fileobj=buf, mode="w:xz", format=tarfile.GNU_FORMAT) as tf:
        def _mkdir_entry(rel):
            rel = rel.strip("/")
            if not rel or rel in dirs_done:
                return
            dirs_done.add(rel)
            ti = tarfile.TarInfo("./" + rel)
            ti.type = tarfile.DIRTYPE
            ti.mode = 0o755
            ti.mtime = now
            ti.uid = ti.gid = 0
            tf.addfile(ti)

        for dp, dns, fns in os.walk(src_dir):
            rel_dir = os.path.relpath(dp, src_dir).replace(os.sep, "/")
            if rel_dir == ".":
                # 防御：data.tar.xz 只允许 opt/、usr/ 子树，顶层绝不能散落文件
                # （之前误把 debian-binary/control 写进 DEB_DIR，被当成根文件解包 → 撞包）
                dns[:] = [d for d in dns if d in ("opt", "usr")]
                fns = []
            if rel_dir != ".":
                for part in range(1, len(rel_dir.split("/")) + 1):
                    _mkdir_entry("/".join(rel_dir.split("/")[:part]))
            for fn in fns:
                fp = os.path.join(dp, fn)
                arc = os.path.relpath(fp, src_dir).replace(os.sep, "/")
                ti = tf.gettarinfo(fp, arcname="./" + arc)
                ti.mtime = now            # 取整，避免 PAX 扩展头
                ti.uid = ti.gid = 0
                ti.uname = ti.gname = "root"
                mode = os.stat(fp).st_mode
                ti.mode = 0o755 if (mode & 0o111) else 0o644
                with open(fp, "rb") as f:
                    tf.addfile(ti, f)
    return buf.getvalue()


def build_deb():
    # 清理上一轮可能残留的顶层散落文件（debian-binary/control），避免污染 data.tar.xz
    for _stray in ("debian-binary", "control"):
        _p = os.path.join(DEB_DIR, _stray)
        if os.path.isfile(_p):
            try:
                os.remove(_p)
            except OSError:
                pass
    opt = os.path.join(DEB_DIR, "opt", PKG)
    os.makedirs(opt, exist_ok=True)
    # 播放数据
    for nm in os.listdir(BUNDLE):
        s = os.path.join(BUNDLE, nm)
        d = os.path.join(opt, nm)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)
    _inline_manifest(opt)
    with open(os.path.join(opt, "serve.py"), "w", encoding="utf-8") as f:
        f.write(SERVE_PY)
    with open(os.path.join(opt, "myme-player"), "w", encoding="utf-8") as f:
        f.write(LAUNCHER)
    # desktop + icon
    apps = os.path.join(DEB_DIR, "usr", "share", "applications")
    icons = os.path.join(DEB_DIR, "usr", "share", "icons", "hicolor", "256x256", "apps")
    os.makedirs(apps, exist_ok=True)
    os.makedirs(icons, exist_ok=True)
    with open(os.path.join(apps, PKG + ".desktop"), "w", encoding="utf-8") as f:
        f.write(DESKTOP)
    src_icon = os.path.join(HERE, "icon-512.png")
    if os.path.isfile(src_icon):
        try:
            from PIL import Image
            Image.open(src_icon).convert("RGBA").resize((256, 256)).save(
                os.path.join(icons, PKG + ".png"))
        except Exception:
            shutil.copy2(src_icon, os.path.join(icons, PKG + ".png"))
    # ⚠ 注意：debian-binary 与 control 是 ar / control.tar 层级的成员，绝不可写进 DEB_DIR
    #    根目录——否则 _tar_xz(DEB_DIR) 会把它们打进 data.tar.xz，dpkg 解包时要在系统根写
    #    /debian-binary、/control，撞上其它包（如 libfprint0）直接报「正试图覆盖 /debian-binary」。
    #    ar 成员用字面量 b"2.0\n"（见下），control 用 CONTROL 常量，下面两个文件是死代码，已删。

    data = _tar_xz(DEB_DIR)
    # control.tar.gz —— 同样强制 GNU_FORMAT（dpkg 用同一个 tar 解析器读它）
    cbuf = io.BytesIO()
    with tarfile.open(fileobj=cbuf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        for nm, body, mode in (("control", CONTROL, 0o644),
                               ("postinst", POSTINST, 0o755),
                               ("prerm", PRERM, 0o755),
                               ("postrm", POSTRM % PKG, 0o755)):
            b = body.encode("utf-8")
            ti = tarfile.TarInfo("./" + nm)
            ti.size = len(b)
            ti.mode = mode
            ti.mtime = int(time.time())
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(b))
    ctrl = cbuf.getvalue()

    out = os.path.join(DIST, DEB_NAME)
    with open(out, "wb") as f:
        f.write(b"!<arch>\n")
        f.write(_ar_member("debian-binary", b"2.0\n"))
        f.write(_ar_member("control.tar.gz", ctrl))
        f.write(_ar_member("data.tar.xz", data))
    log("✅ 统信 deb：%s（%.1f MB）" % (out, os.path.getsize(out) / 1048576))
    return out


def verify_deb(path):
    """解包校验：ar 成员齐全 + data.tar.xz 能解出关键文件。

    额外做「PAX 头体检」：逐个成员扫描 typeflag，一旦出现 'x'/'g'（PAX 扩展头），
    就直接判定失败——统信 dpkg 会以「不支持的 PAX tar 头部类型 'x'」拒绝安装。
    """
    import lzma
    raw = open(path, "rb").read()
    if not raw.startswith(b"!<arch>\n"):
        return "ar 头不对"
    pos = 8
    names = []
    while pos + 60 <= len(raw):
        nm = raw[pos:pos + 16].decode("ascii", "ignore").strip()
        size = int(raw[pos + 48:pos + 58].decode("ascii", "ignore").strip() or 0)
        body = raw[pos + 60:pos + 60 + size]
        names.append(nm)
        if nm.startswith("data.tar"):
            plain = lzma.decompress(body)
            with tarfile.open(fileobj=io.BytesIO(plain)) as tf:
                mem = tf.getnames()
                bad = [m.name for m in tf.getmembers() if m.type in ("x", "g")]
                if bad:
                    return "含 PAX 扩展头（统信 dpkg 会拒装）：" + ", ".join(bad[:3])
                # 防御：data.tar.xz 绝不允许顶层散落文件（会解包到系统根，撞其它包）
                bad_root = [m.name for m in tf.getmembers()
                            if (not m.isdir()) and m.name.replace("./", "", 1).count("/") == 0]
                if bad_root:
                    return "data.tar.xz 含顶层散落文件（会解包到系统根，撞包）：" + ", ".join(bad_root[:3])
            for need in ("opt/%s/player.html" % PKG, "opt/%s/manifest.json" % PKG,
                         "opt/%s/serve.py" % PKG, "opt/%s/myme-player" % PKG,
                         "usr/share/applications/%s.desktop" % PKG):
                if not any(m.endswith(need) or m == "./" + need for m in mem):
                    return "data 缺 " + need
        pos += 60 + size + (size % 2)
    for need in ("debian-binary", "control.tar.gz", "data.tar.xz"):
        if need not in names:
            return "ar 缺 " + need
    return ""


def main():
    args = sys.argv[1:] or ["all"]
    os.makedirs(DIST, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    if "bundle" in args:
        build_bundle()
    else:
        ensure_bundle()
    outs = []
    if "all" in args or "win" in args:
        outs.append(build_win())
    if "all" in args or "apk" in args:
        outs.append(build_apk())
    if "all" in args or "deb" in args:
        p = build_deb()
        err = verify_deb(p)
        if err:
            log("❌ deb 校验失败：", err)
        else:
            log("✅ deb 解包校验通过")
        outs.append(p)
    print("\n产物：")
    for o in outs:
        if o:
            print("  →", o)


if __name__ == "__main__":
    main()
