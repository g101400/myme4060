# -*- coding: utf-8 -*-
"""把源码打成可分发 zip（publish_release.py 上传时的「myme_source_<ver>.zip」）。

为什么单独立这个脚本：发布链里只有「打包」没有「打源码」，
之前每版都是临时手工 zip，版本对不上、漏文件是常事。

用法：
  python pack_source.py              # 用 config.VERSION 命名
  python pack_source.py 2.6.9        # 指定版本号
输出：dist/myme_source_<ver>.zip
"""
import os
import sys
import zipfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

VER = sys.argv[1] if len(sys.argv) > 1 else config.VERSION
OUT = os.path.join(HERE, "dist", "myme_source_%s.zip" % VER)

# 排除：构建产物 / 版本库 / 缓存 / 体积大头（_pkg.7z 292MB 绝不能进源码包）
SKIP_DIRS = {"dist", "dist_pkg", "dist269", "dist269p", "build", "build269", "build269p",
             ".git", "__pycache__", "android-player", "_pkg", "node_modules",
             ".venv", "venv", "_ios_stage", "demo_avatar"}
SKIP_EXT = {".7z", ".exe", ".msi", ".zip", ".apk", ".deb", ".png", ".jpg", ".mp4", ".wav"}
INCLUDE_EXT = {".py", ".html", ".json", ".bat", ".md", ".txt", ".spec", ".sh", ".svg",
               ".gitignore", ".rc", ".toml", ".cfg", ".ini", ".yml", ".yaml", ".css", ".js"}
# 运行期资源（声纹 / PWA / 图标 / 提示词）也要进包，否则拿源码包起不来服务。
# 按「目录 + 单文件上限」放行，避免把 292MB 的 _pkg.7z 之类的大件带进来。
ALWAYS = {"icon-192.png", "icon-512.png", "sw.js", "manifest.json"}
ASSET_DIRS = {"voice", "pwa", "prompts", "ui", "ppt_templates", "sound"}
# ⚠ 上限别设太小：炎冰声纹 yanbing-sample-01.wav 就有 10.4MB，曾被 5MB 的阈值
# 静默丢掉、只剩 2.9MB 的 jinzi 进包 —— 拿源码包克隆声音时缺主参考音。
# 大件已由 SKIP_EXT（.7z/.exe/.mp4…）与 SKIP_DIRS（_pkg/dist*）挡住，这里只兜底。
MAX_ASSET = 64 * 1048576


def walk():
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("_pkg")]
        for fn in sorted(files):
            if fn in ALWAYS:
                yield root, fn
                continue
            # 运行期资源（声纹 / PWA / 模板 / 图标）先放行：这类文件常被 SKIP_EXT
            # 里的 .wav / .png 规则误杀，导致拿源码包起不来服务。
            parent = os.path.basename(root).lower()
            if parent in ASSET_DIRS and fn not in ("__pycache__",):
                yield root, fn
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT:
                continue
            if ext in INCLUDE_EXT or fn in (".gitignore", "version_info.txt"):
                yield root, fn


def main():
    t0 = time.time()
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, fn in sorted(walk()):
            full = os.path.join(root, fn)
            arc = os.path.relpath(full, HERE)
            try:
                if os.path.getsize(full) > MAX_ASSET and not arc.startswith(("src/", "lib/")):
                    continue
                z.write(full, arc)
                n += 1
            except Exception as e:
                print("  跳过（打不进）：", arc, e)
    size = os.path.getsize(OUT)
    print("[pack_source] %s（%d 文件，%.1f MB，%.1fs）"
          % (OUT, n, size / 1048576, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
