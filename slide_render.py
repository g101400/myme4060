# -*- coding: utf-8 -*-
"""PPT → 原稿版式页图 渲染管线。

链路：pptx/doc(x) → LibreOffice headless 转 PDF（绝对路径，静默失败需检测）→
     pymupdf(MuPDF 引擎) 逐页光栅化为 PNG。

输出目录：SLIDE_IMG/<md5源文件>/pNN.png
供 app.py 的 /api/slides 使用：每页附 img 字段（/api/img 相对 URL）。

坑位记录（勿重蹈）：
- soffice 收到相对路径会静默失败（EXIT=0 但无产物），源与 outdir 都必须绝对路径；
- soffice 输出文件名跟随"源文件名"，固定名 deck.pptx → deck.pdf 才好定位；
- 系统自带的 convert.exe 是磁盘工具不是 ImageMagick；Windows 无 poppler 时用 pymupdf。
"""
import hashlib
import os
import glob
import shutil
import subprocess
import time

import pymupdf  # PyMuPDF 1.28+

HERE = os.path.dirname(os.path.abspath(__file__))
SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]
SLIDE_IMG_ROOT = os.path.join(HERE, "slide_img")
DPI = 110  # 1467x825 @960x540pt，兼顾清晰度与体积（~440KB/页）


def find_soffice():
    for p in SOFFICE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _clean_env():
    """soffice 自带 Python 运行时会被外部 PYTHONPATH/PYTHONHOME/PYTHONUTF8 等变量污染，
    导致 "Could not find platform independent libraries"。调 soffice 前清理这些变量，
    并把 PYTHONHOME 指回 soffice 自带的 python-core，使其能正确 bootstrap。"""
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("PYTHON"):
            e.pop(k, None)
    core = None
    for gd in glob.glob(os.path.join(SOFFICE_CANDIDATES[0].replace("soffice.exe", ""),
                                     "python-core-*")):
        core = gd
    if core:
        e["PYTHONHOME"] = core
        e["PYTHONPATH"] = os.path.join(core, "lib")
    return e


def _pptx_to_pdf(src_abs, out_dir):
    """LibreOffice headless：pptx/ppt/doc(x) → pdf。必须绝对路径！"""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("未找到 LibreOffice (soffice.exe)，无法渲染原稿版式")
    # 源复制为固定名 deck.pptx，输出才会是 deck.pdf
    deck = os.path.join(out_dir, "deck.pptx")
    shutil.copy(src_abs, deck)
    # 清理外部 Python 环境变量，避免污染 soffice 自带 Python 运行时
    env = _clean_env()
    r = subprocess.run(
        [soffice, "--headless", "--norestore",
         "--convert-to", "pdf", "--outdir", out_dir, deck],
        capture_output=True, timeout=300, env=env)
    pdf = os.path.join(out_dir, "deck.pdf")
    if not os.path.exists(pdf):
        err = (r.stderr or b"").decode("utf-8", "ignore")[-200:]
        raise RuntimeError("soffice 转 PDF 失败（注意必须绝对路径）：%s" % err)
    return pdf


def render_deck(src_abs, dpi=DPI, progress=None):
    """渲染整个文档为页图。

    返回 (img_dir, [png 绝对路径...])。img_dir 为 SLIDE_IMG/<md5>/。
    progress: 可选回调 fn(cur, total) 用于后台任务更新进度。
    """
    src_abs = os.path.abspath(src_abs)
    with open(src_abs, "rb") as f:
        tag = hashlib.md5(f.read(1 << 20)).hexdigest()[:12]  # 前 1MB 足够区分版本
    out_dir = os.path.join(SLIDE_IMG_ROOT, tag)
    os.makedirs(out_dir, exist_ok=True)

    # —— 页图缓存：上次已完整渲染（无残留中间产物）则直接复用，秒级返回 ——
    leftover = [f for f in ("deck.pdf", "deck.pptx")
                if os.path.exists(os.path.join(out_dir, f))]
    if not leftover:
        cached = sorted(glob.glob(os.path.join(out_dir, "p*.png")))
        if cached:
            print("[slide_render] 复用页图缓存 %d 页 -> %s" % (len(cached), out_dir))
            if progress:
                try:
                    progress(len(cached), len(cached))
                except Exception:
                    pass
            return out_dir, cached
    else:
        # 上次渲染中途失败/被杀，残留中间产物：清掉旧页图重新渲染，避免新旧混杂
        for old in glob.glob(os.path.join(out_dir, "p*.png")):
            try:
                os.remove(old)
            except OSError:
                pass

    t0 = time.time()
    pdf = _pptx_to_pdf(src_abs, out_dir)
    doc = pymupdf.open(pdf)
    paths = []
    total = doc.page_count
    for i, pg in enumerate(doc, 1):
        fp = os.path.join(out_dir, "p%02d.png" % i)
        pg.get_pixmap(dpi=dpi).save(fp)
        paths.append(fp)
        if progress:
            try:
                progress(i, total)
            except Exception:
                pass
    doc.close()
    # 清理中间产物，只留页图。删除失败（文件被占用/被删除保护拦截）绝不能
    # 让整个任务卡死——昨天 30/30 页渲染完成后就卡在 os.remove 上 20 多小时。
    for fp in (pdf, os.path.join(out_dir, "deck.pptx")):
        try:
            os.remove(fp)
        except OSError as e:
            print("[slide_render] 中间产物清理失败（不影响结果）：%s" % e)
    print("[slide_render] %d 页渲染完成 %.1fs -> %s" % (total, time.time() - t0, out_dir))
    return out_dir, paths


if __name__ == "__main__":
    import sys
    d, ps = render_deck(sys.argv[1])
    print(d)
    for p in ps[:3]:
        print("  ", p)
