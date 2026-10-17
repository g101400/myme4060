# -*- coding: utf-8 -*-
"""构建 v2.9.3 交接包：D:/Users/WorkBuddy/handover_myme_2026-10-16/
结构沿用 v2.9.2 包约定：编号文档（00 主交接 + 01~17 历史 + 18 本版）+ README + 手册 + 三端对照表 + 精选源码 zip。
"""
import os, shutil, zipfile, sys

SRC = r'D:\Users\WorkBuddy\Claw\myme'
OLD = r'D:\Users\WorkBuddy\handover_myme_2026-10-15'
OUT = r'D:\Users\WorkBuddy\handover_myme_2026-10-16'
VER = '2.9.3'

# ⚠ 输出目录只做"增量覆盖"，**不要整目录 rmtree**：
#   00_主交接文档.md 与 18_v2.9.3_变更与交付清单.md 是手写的，重跑本脚本会把它们连锅端掉。
os.makedirs(OUT, exist_ok=True)

# ---------- 1) 历史编号文档原样沿用（01~17；00 另处理） ----------
hist = [f for f in os.listdir(OLD)
        if f[:2].isdigit() and f.endswith('.md') and not f.startswith('00_')]
for f in sorted(hist):
    shutil.copy2(os.path.join(OLD, f), os.path.join(OUT, f))
print('沿用历史文档 %d 份：%s … %s' % (len(hist), sorted(hist)[0], sorted(hist)[-1]))

# ---------- 2) 当前 README / 手册 / 三端对照表 ----------
for f in ('README.md', '安装与配置手册.md', '三端对照表.md'):
    p = os.path.join(SRC, f)
    if os.path.isfile(p):
        shutil.copy2(p, os.path.join(OUT, f))
        print('已带入', f)

# ---------- 3) 精选源码 zip（沿用 v2.9.2 的排除式筛选口径） ----------
ZIP = os.path.join(OUT, 'myme_src_%s.zip' % VER)
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'build', 'build269', 'build269b',
             'build269c', 'build269cp', 'build269p', 'build269pb', 'dist', 'dist269',
             'dist269b', 'dist269c', 'dist269cp', 'dist269p', 'dist269pb', 'dist_pkg',
             '_pkg', '_ios_stage', 'data', 'LivePortrait', 'ts_deps', 'projects',
             'handover_myme_2026-10-06', 'handover_myne_2026-10-06', 'handover_myne_2026-10-15',
             '_backup_v292_20261015', '_backup_v293_20261016', '_rc_probe_20261016'}
SKIP_EXT = {'.7z', '.exe', '.msi', '.zip', '.apk', '.deb', '.mp4', '.log', '.bak',
            '.pyc', '.class', '.idsig', '.pkg', '.pyz'}
SKIP_FILES = {'README.md', '安装与配置手册.md', '三端对照表.md'}
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

# ---------- 4) 重打总包 zip ----------
TOTAL = os.path.join(OUT, 'myme_数字分身_交接_v%s_20261016.zip' % VER)
# ⚠ 先列清单再开写，否则刚建的空 zip 会被自己收进去
items = [fn for fn in sorted(os.listdir(OUT))
         if os.path.isfile(os.path.join(OUT, fn))
         and fn != os.path.basename(TOTAL)]
cnt = 0
with zipfile.ZipFile(TOTAL, 'w', zipfile.ZIP_DEFLATED, compresslevel=4) as z:
    for fn in items:
        z.write(os.path.join(OUT, fn), fn); cnt += 1
print('交接总包：%s（%d 项，%.1f MB）' % (TOTAL, cnt, os.path.getsize(TOTAL) / 1048576))
print('交接包目录：', OUT)
