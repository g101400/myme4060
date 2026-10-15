# -*- coding: utf-8 -*-
"""构建 v2.9.2 交接包：D:/Users/WorkBuddy/handover_myme_2026-10-15/
结构沿用 v2.8.1 包约定：编号文档（00 主交接 + 01~16 历史 + 17 本版）+ README + 手册 + 精选源码 zip。
"""
import io, os, shutil, zipfile, sys

SRC = r'D:\Users\WorkBuddy\Claw\myme'
OLD = r'D:\Users\myme_数字分身_交接_v2.8.1_20261008\handover_myme_2026-10-08b'
OUT = r'D:\Users\WorkBuddy\handover_myme_2026-10-15'
VER = '2.9.2'

# ⚠ 输出目录只做"增量覆盖"，**不要整目录 rmtree**：
#   00_主交接文档.md 与 17_v2.9.2_变更与交付清单.md 是手写的，重跑本脚本会把它们连锅端掉
#   （2026-10-16 真踩过一次，靠最终 zip 才捞回来）。要重建请先手动删目录。
os.makedirs(OUT, exist_ok=True)

# ---------- 1) 历史编号文档原样沿用 ----------
hist = [f for f in os.listdir(OLD) if f[:2].isdigit() and f.endswith('.md') and not f.startswith('00_')]
for f in sorted(hist):
    shutil.copy2(os.path.join(OLD, f), os.path.join(OUT, f))
print('沿用历史文档 %d 份：%s … %s' % (len(hist), sorted(hist)[0], sorted(hist)[-1]))

# ---------- 2) 当前 README / 手册 ----------
for f in ('README.md', '安装与配置手册.md'):
    shutil.copy2(os.path.join(SRC, f), os.path.join(OUT, f))
print('已带入当前 README.md / 安装与配置手册.md')

# ---------- 3) 精选源码 zip（排除式筛选，沿用 v2.8.1 包 myme_src 约定） ----------
# v2.8.1 包的 myme_src 是"全量源码目录"：根级全部 .py + ui/voice/templates/
# android-player 源码 + 15MB 模板 pptx + 10.4MB 炎冰声纹。
# ⚠ 阈值教训（pack_source.py 注释）：炎冰声纹 10.4MB 曾被 5MB 阈值静默丢掉，
#   导致拿源码包克隆声音时缺主参考音 —— MAX_FILE 必须 ≥ 16MB。
ZIP = os.path.join(OUT, 'myme_src_%s.zip' % VER)
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'build', 'build269', 'build269b',
             'build269c', 'build269cp', 'build269p', 'build269pb', 'dist', 'dist269',
             'dist269b', 'dist269c', 'dist269cp', 'dist269p', 'dist269pb', 'dist_pkg',
             '_pkg', '_ios_stage', 'data', 'LivePortrait', 'ts_deps', 'projects',
             'handover_myme_2026-10-06', 'handover_myne_2026-10-06', 'handover_myne_2026-10-15'}
SKIP_EXT = {'.7z', '.exe', '.msi', '.zip', '.apk', '.deb', '.mp4', '.log', '.bak',
            '.pyc', '.class', '.idsig', '.pkg', '.pyz'}
# 根级 README/手册已在包顶层，zip 内不重复收录
SKIP_FILES = {'README.md', '安装与配置手册.md'}
MAX_FILE = 32 * 1048576   # 覆盖 templates pptx 15MB + 炎冰声纹 10.4MB

n = 0
with zipfile.ZipFile(ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, SRC)
        for fn in sorted(files):
            if fn in SKIP_FILES and rel == '.':
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SKIP_EXT:
                continue
            p = os.path.join(root, fn)
            if os.path.getsize(p) > MAX_FILE:
                print('  跳过(>%dMB):' % (MAX_FILE // 1048576), os.path.relpath(p, SRC)); continue
            z.write(p, os.path.relpath(p, SRC))
            n += 1
print('源码 zip：%s（%d 个文件，%.1f MB）' % (os.path.basename(ZIP), n, os.path.getsize(ZIP) / 1048576))
print('交接包目录：', OUT)
