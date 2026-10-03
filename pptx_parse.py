# -*- coding: utf-8 -*-
"""
myme.pptx_parse — 解析演示文稿（纯标准库，无需 python-pptx）
PPTX 本质是 ZIP 包：文本在 ppt/slides/slideN.xml 的 <a:t> 元素里，
演讲者备注在 ppt/notesSlides/notesSlideN.xml 里（经 slideN.xml.rels 关联）。
输出：list[{idx, title, text, notes, images:[path]}]
"""
import os
import re
import zipfile
import json
import config

# 用 ElementTree 时命名空间会带前缀，统一取本地名
def _local(tag):
    return tag.split('}', 1)[1] if '}' in tag else tag


def _text_of(elem):
    """收集 elem 子树里所有 <a:t> 的文本。"""
    parts = []
    for el in elem.iter():
        if _local(el.tag) == 't' and el.text:
            parts.append(el.text)
    return ''.join(parts)


def _slide_numbers(z):
    """ppt/slides/slideN.xml 按数字排序。"""
    names = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    def num(n):
        m = re.search(r'slide(\d+)\.xml$', n)
        return int(m.group(1)) if m else 0
    return sorted(names, key=num)


def _rels_of(z, rels_name):
    """读取某个 .rels，返回 list[{type, target}]（属性顺序无关）。"""
    try:
        rels = z.read(rels_name).decode('utf-8')
    except KeyError:
        return []
    out = []
    for relstr in re.findall(r'<Relationship\b[^>]*/>', rels):
        t = re.search(r'Type="([^"]*)"', relstr)
        tg = re.search(r'Target="([^"]*)"', relstr)
        if t and tg:
            out.append((t.group(1), tg.group(1)))
    return out


def _notes_for(z, slide_idx):
    """读取该页备注文本。"""
    rels = _rels_of(z, f'ppt/slides/_rels/slide{slide_idx}.xml.rels')
    target = None
    for rtype, rt in rels:
        if 'notesSlide' in rtype:
            target = rt
            break
    if not target:
        return ''
    path = os.path.normpath(os.path.join('ppt/slides', target)).replace('\\', '/')
    try:
        root = _et(z.read(path))
    except (KeyError, Exception):
        return ''
    return _text_of(root)


def _images_for(z, slide_idx, img_dir):
    """抽取该页引用的图片到 img_dir，返回本地路径列表。"""
    rels = _rels_of(z, f'ppt/slides/_rels/slide{slide_idx}.xml.rels')
    imgs = []
    for rtype, rt in rels:
        if 'image' not in rtype:
            continue
        path = os.path.normpath(os.path.join('ppt/slides', rt)).replace('\\', '/')
        try:
            blob = z.read(path)
        except KeyError:
            continue
        fn = os.path.basename(path)
        # 避免不同页同名覆盖
        out = os.path.join(img_dir, f"slide{slide_idx}_{fn}")
        with open(out, 'wb') as f:
            f.write(blob)
        imgs.append(out)
    return imgs


def _et(data):
    import xml.etree.ElementTree as ET
    return ET.fromstring(data if isinstance(data, bytes) else data.encode('utf-8'))


def parse_pptx(path, img_dir=None):
    base = os.path.splitext(os.path.basename(path))[0]
    if img_dir is None:
        # 随当前项目动态定位（config.DIR_KB 由项目系统重算），不再硬编码 WORKSPACE/kb_data
        img_dir = os.path.join(config.DIR_KB, "imgs_" + base)
    os.makedirs(img_dir, exist_ok=True)
    slides = []
    with zipfile.ZipFile(path) as z:
        for sn in _slide_numbers(z):
            idx = int(re.search(r'slide(\d+)\.xml$', sn).group(1))
            root = _et(z.read(sn))
            title, texts = "", []
            for sp in root.iter():
                if _local(sp.tag) != 'sp':
                    continue
                txt = _text_of(sp)
                if not txt:
                    continue
                ph_type = ''
                for ph in sp.iter():
                    if _local(ph.tag) == 'ph' and ph.get('type'):
                        ph_type = ph.get('type')
                        break
                if ph_type in ('title', 'ctrTitle') and not title:
                    title = txt
                elif not title and len(txt) <= 40:
                    title = txt
                else:
                    texts.append(txt)
            notes = _notes_for(z, idx)
            images = _images_for(z, idx, img_dir)
            slides.append({
                "idx": idx,
                "title": title,
                "text": "\n".join(texts),
                "notes": notes,
                "images": images,
            })
    return slides


def parse_pdf(path):
    raise RuntimeError("当前为纯标准库版本，未集成 PDF 解析。请先把 PDF 导出为 PPTX 再上传。")


def parse(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".pptx", ".ppt"):
        return parse_pptx(path)
    if ext == ".pdf":
        return parse_pdf(path)
    raise ValueError(f"不支持的文件类型: {ext}（仅支持 .pptx/.ppt）")


def save_json(slides, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(slides, f, ensure_ascii=False, indent=2)
    return out_path


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else None
    if not p:
        print("用法: python pptx_parse.py <file.pptx>")
    else:
        s = parse(p)
        print(f"共 {len(s)} 页")
        for sl in s[:3]:
            print(f"--- 第{sl['idx']}页 {sl['title']} ---")
            print(sl["text"][:120])
            if sl["notes"]:
                print("备注:", sl["notes"][:80])
