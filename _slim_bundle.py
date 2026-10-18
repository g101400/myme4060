# -*- coding: utf-8 -*-
"""把示例播放包里的逐页大图压成"够看又不卡"的尺寸（v2.9.5 新增）。

为什么要有这一步（不是为了好看，是为了能装能跑）：
  · 19 页水利 PPT 的整页截图，原图 112 MB（单张 3~8 MB）。三个后果：
      1) 统信 deb / 安卓 apk 从 2.4 MB 涨到 140 MB，五个架构合计 700 MB，上传与安装都受罪；
      2) 龙芯 3A400 + 8 GB 无独显，浏览器解码 8 MB 的 PNG 会明显卡顿（用户明确要求"不卡顿"）；
      3) 手机端 APK 超过 100 MB，分发渠道普遍不收。
  · 演示场景真正需要的是"看得清字"，不是原图分辨率。整页截图在 1600px 宽、JPEG q=86 下
    文字仍然清晰，单页降到 200~400 KB。

做法（保守，不破坏任何引用）：
  1) 只处理 `images/` 下超过阈值或宽度超过 MAXW 的图；
  2) 输出同名 `.jpg`，删除原图，**同时改写 manifest.json / slides.json / manifest.js 里的引用**；
  3) 最后做一次"引用完整性"断言：清单里出现的每个图都必须真实存在，否则整体失败（非零退出）。

用法： python3 _slim_bundle.py [包目录]        # 默认 C:/tmp/myme_pkg/portable
"""
import io
import json
import os
import re
import sys

DEFAULT = r"C:/tmp/myme_pkg/portable"
MAXW = 1600           # 目标最大宽度（1080p 投影足够）
BYTES_LIMIT = 700 * 1024
QUALITY = 86


def refs_to_fix(root):
    """返回需要改写的清单文件列表（只改这三份）。"""
    out = []
    for n in ("manifest.json", "slides.json", "manifest.js"):
        p = os.path.join(root, n)
        if os.path.isfile(p):
            out.append((n, p))
    return out


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    img_dir = os.path.join(root, "images")
    if not os.path.isdir(img_dir):
        raise SystemExit("找不到 images 目录：%s" % img_dir)

    try:
        from PIL import Image
    except Exception as e:
        raise SystemExit("需要 Pillow：pip install pillow（%s）" % e)

    before = sum(os.path.getsize(os.path.join(img_dir, f)) for f in os.listdir(img_dir))
    rename = {}     # 旧文件名 -> 新文件名
    n_done = n_skip = 0

    for fn in sorted(os.listdir(img_dir)):
        p = os.path.join(img_dir, fn)
        if not os.path.isfile(p):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            continue
        sz = os.path.getsize(p)
        try:
            im = Image.open(p)
        except Exception as e:
            print("  ⚠ 打不开，跳过：%s（%s）" % (fn, e))
            n_skip += 1
            continue
        w, h = im.size
        if sz <= BYTES_LIMIT and w <= MAXW and ext in (".jpg", ".jpeg"):
            n_skip += 1
            continue
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if w > MAXW:
            im = im.resize((MAXW, max(1, int(h * MAXW / float(w)))), Image.LANCZOS)
        stem = os.path.splitext(fn)[0]
        new_fn = stem + ".jpg"
        new_p = os.path.join(img_dir, new_fn)
        im.save(new_p, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        if new_fn != fn and os.path.isfile(p):
            os.remove(p)
        elif new_fn == fn:
            pass
        rename[fn] = new_fn
        n_done += 1
        print("  · %-14s %6.1f KB → %6.1f KB  (%dx%d)" % (
            fn, sz / 1024.0, os.path.getsize(new_p) / 1024.0, im.size[0], im.size[1]))

    # ---- 改写引用 ----
    def repl(text):
        for old, new in rename.items():
            # 只替换文件名本身，避免误伤目录名
            text = text.replace("images/" + old, "images/" + new)
            text = text.replace('"' + old + '"', '"' + new + '"')
        return text

    for name, p in refs_to_fix(root):
        s = io.open(p, encoding="utf-8").read()
        s2 = repl(s)
        if s2 != s:
            io.open(p, "w", encoding="utf-8").write(s2)
            print("  ✎ 已改写引用：%s" % name)

    after = sum(os.path.getsize(os.path.join(img_dir, f)) for f in os.listdir(img_dir))
    print("\n图片：%.1f MB → %.1f MB（处理 %d 张，跳过 %d 张）"
          % (before / 1048576.0, after / 1048576.0, n_done, n_skip))

    # ---- 引用完整性断言（缺一张就让整条链红掉） ----
    bad = []
    for name, p in refs_to_fix(root):
        s = io.open(p, encoding="utf-8").read()
        for m in re.finditer(r"images/([^\"'\\\s,)]+)", s):
            fp = os.path.join(img_dir, m.group(1))
            if not os.path.isfile(fp) and m.group(1) not in bad:
                bad.append(m.group(1))
    if bad:
        print("\n❌ 引用完整性失败，缺 %d 张图：%s" % (len(bad), bad[:8]))
        raise SystemExit(1)
    print("✅ 引用完整性 OK（清单里每个 images/... 都真实存在）")

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fns in os.walk(root) for f in fns)
    print("包总体积：%.1f MB → 目标 <60 MB" % (total / 1048576.0))


if __name__ == "__main__":
    main()
