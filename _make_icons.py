# -*- coding: utf-8 -*-
"""把 ComfyUI 生成的图标图裁成应用图标：icon-512 / icon-192 / myme.ico。

做法：自动找到圆角方块的边界 -> 裁出 -> 套圆角 alpha -> 多尺寸导出。
"""
import os
import sys

from PIL import Image, ImageDraw

SRC = sys.argv[1] if len(sys.argv) > 1 else "test_out/icon_gen/zi_20261001.png"
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

im = Image.open(SRC).convert("RGBA")
W, H = im.size
px = im.load()

# 1) 找圆角方块边界：以四角颜色为背景参考，扫描行/列是否与背景明显不同
bg = px[3, 3]
def diff(a, b):
    return sum(abs(a[i] - b[i]) for i in range(3))

def row_has_content(y, x0, x1, step=8):
    n = 0
    for x in range(x0, x1, step):
        if diff(px[x, y], bg) > 40:
            n += 1
    return n > (x1 - x0) // step // 3

def col_has_content(x, y0, y1, step=8):
    n = 0
    for y in range(y0, y1, step):
        if diff(px[x, y], bg) > 40:
            n += 1
    return n > (y1 - y0) // step // 3

top = next((y for y in range(0, H // 2, 2) if row_has_content(y, 0, W)), 0)
bot = next((y for y in range(H - 1, H // 2, -2) if row_has_content(y, 0, W)), H - 1)
lft = next((x for x in range(0, W // 2, 2) if col_has_content(x, top, bot)), 0)
rgt = next((x for x in range(W - 1, W // 2, -2) if col_has_content(x, top, bot)), W - 1)
# 补齐到正方形（贴边时向外扩），保证圆角完整不被裁掉
bw, bh = rgt - lft, bot - top
side = max(bw, bh)
cx, cy = (lft + rgt) // 2, (top + bot) // 2
half = side // 2
box = (max(0, cx - half), max(0, cy - half),
       min(W, cx + half), min(H, cy + half))
print("tile box:", lft, top, rgt, bot, "->", box)

tile = im.crop(box)
side = min(tile.size)
tile = tile.crop((0, 0, side, side)) if tile.size[0] >= tile.size[1] else \
       tile.crop(((tile.size[0]-side)//2, 0, (tile.size[0]-side)//2 + side, side))

# 2) 圆角 alpha（半径约边长的 22%，接近 iOS/Windows 图标观感）
def rounded(img, radius_ratio=0.22):
    s = img.size[0]
    r = int(s * radius_ratio)
    mask = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out

tile = rounded(tile)

# 3) 输出
tile.resize((512, 512), Image.LANCZOS).save("icon-512.png")
tile.resize((192, 192), Image.LANCZOS).save("icon-192.png")
tile.resize((256, 256), Image.LANCZOS).save(
    "myme.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
tile.resize((512, 512), Image.LANCZOS).save(
    os.path.join("android-player", "res", "drawable", "icon.png"))
print("OK icon-512 / icon-192 / myme.ico / android icon")
