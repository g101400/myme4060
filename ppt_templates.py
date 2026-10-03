# -*- coding: utf-8 -*-
"""
myme.ppt_templates —— PPT 模板 / 主题管理（v2.6）

两层来源：
  1. 内置主题   —— pptx_write.THEMES 里的配色方案（蓝 / 灰 / 暗），零文件、随时可用
  2. 自定义模板 —— 用户传入自己的 .pptx，系统把它的「主题 + 母版 + 版式」移植到生成的 PPT 上，
                   从而继承自家模板的字体、主色、版式边距、背景等，而不是被迫用系统配色

移植策略（`apply_template`）：
  生成端照旧产出一份自包含的最小 pptx，然后做"换皮"——
  保留我们写的 slides（内容完全由系统生成），
  把 theme / slideMaster / slideLayout 及其依赖（rels、media）整体替换成模板的，
  并重排 slide→layout 的关系 ID，使每张幻灯片挂到模板的第一个版式上。

这样既不用去 reverse engineering 别人的版式，也不会引入 python-pptx 依赖。
"""
import os
import json
import re
import shutil
import zipfile
import uuid

import config

TEMPLATE_DIR = os.path.join(config.DATA_DIR, "templates")
TEMPLATE_INDEX = os.path.join(TEMPLATE_DIR, "templates.json")

# 内置主题（与 pptx_write.THEMES 的 key 对应）
BUILTIN = [
    {"id": "blue", "name": "内置 · 商务蓝", "builtin": True, "desc": "深蓝主色，通用汇报"},
    {"id": "gray", "name": "内置 · 沉稳灰", "builtin": True, "desc": "中性灰，适合正式场合"},
    {"id": "dark", "name": "内置 · 深色剧场", "builtin": True, "desc": "深底浅字，适合大屏演示"},
]


def _ensure_dir():
    os.makedirs(TEMPLATE_DIR, exist_ok=True)


def _load_index():
    try:
        if os.path.exists(TEMPLATE_INDEX):
            with open(TEMPLATE_INDEX, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {"items": []}


def _save_index(idx):
    _ensure_dir()
    try:
        with open(TEMPLATE_INDEX, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def list_templates():
    """返回 [内置主题..., 自定义模板...]，供前端下拉选择。"""
    idx = _load_index()
    items = [dict(x) for x in BUILTIN]
    for it in (idx.get("items") or []):
        p = os.path.join(TEMPLATE_DIR, it.get("file") or "")
        items.append({
            "id": it.get("id"),
            "name": it.get("name") or it.get("id"),
            "builtin": False,
            "desc": ("自定义模板 · %.1f KB" % (os.path.getsize(p) / 1024.0))
                    if os.path.exists(p) else "自定义模板（文件缺失）",
            "missing": not os.path.exists(p),
        })
    return {"templates": items, "dir": TEMPLATE_DIR}


def add_template(src_path, name=None):
    """保存一份自定义模板。返回 (ok, template_id, msg)。"""
    if not src_path or not os.path.exists(src_path):
        return False, "", "模板文件不存在"
    if not src_path.lower().endswith((".pptx", ".potx")):
        return False, "", "模板必须是 .pptx / .potx 文件"
    try:
        with zipfile.ZipFile(src_path) as z:
            names = z.namelist()
        # 至少要有版式或母版，否则换皮后打不开
        if not any(n.startswith("ppt/slideLayouts/") for n in names):
            return False, "", "这个文件里找不到版式（slideLayouts），无法作为模板使用"
    except Exception as e:
        return False, "", "无法读取该文件：%s" % e
    _ensure_dir()
    tid = uuid.uuid4().hex[:10]
    dst = os.path.join(TEMPLATE_DIR, tid + ".pptx")
    try:
        shutil.copy2(src_path, dst)
    except Exception as e:
        return False, "", "保存模板失败：%s" % e
    idx = _load_index()
    items = idx.get("items") or []
    items.append({"id": tid, "name": name or os.path.splitext(os.path.basename(src_path))[0],
                  "file": os.path.basename(dst)})
    idx["items"] = items
    _save_index(idx)
    return True, tid, "模板已保存：%s" % (name or tid)


def delete_template(tid):
    idx = _load_index()
    items = idx.get("items") or []
    keep, target = [], None
    for it in items:
        if it.get("id") == tid:
            target = it
        else:
            keep.append(it)
    idx["items"] = keep
    _save_index(idx)
    if target:
        try:
            os.remove(os.path.join(TEMPLATE_DIR, target.get("file") or ""))
        except Exception:
            pass
    return True


def template_path(tid):
    if not tid or tid in [b["id"] for b in BUILTIN]:
        return ""
    idx = _load_index()
    for it in (idx.get("items") or []):
        if it.get("id") == tid:
            p = os.path.join(TEMPLATE_DIR, it.get("file") or "")
            return p if os.path.exists(p) else ""
    return ""


# ==================== 换皮：把模板的母版/版式/主题移植到产出 PPT ====================
def _referenced_media(tz, tnames):
    """只挑出「母版 / 版式」真正引用到的媒体与字体。

    直接整搬 ppt/media/ 会把模板里成百上千张插图一起背上（实测 5KB 的生成稿
    被撑到 15MB），而幻灯片内容是我们自己生成的，压根用不到那些图。
    这里顺着 slideMasters / slideLayouts 的 rels 反查依赖，只搬被引用项。
    """
    keep = set()
    rel_files = [n for n in tnames
                 if (n.startswith("ppt/slideMasters/") or n.startswith("ppt/slideLayouts/"))
                 and n.endswith(".rels")]
    for rn in rel_files:
        try:
            xml = tz.read(rn).decode("utf-8", "ignore")
        except Exception:
            continue
        for m in re.finditer(r'<Relationship[^>]*Target="([^"]+)"', xml):
            tgt = m.group(1)
            if tgt.startswith("http") or tgt.startswith("/"):
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(rn), tgt)).replace("\\", "/")
            if resolved.startswith("ppt/media/") or resolved.startswith("ppt/fonts/"):
                if resolved in tnames:
                    keep.add(resolved)
    return keep


_THEME_PREFIXES = ("ppt/theme/", "ppt/slideMasters/", "ppt/slideLayouts/")
_RELIP_RE = re.compile(
    r'<Relationship Id="[^"]*" Type="[^"]*(slideLayout)[^"]*" Target="([^"]*)"')


def _pick_layout(names):
    """挑一个可用版式：优先 slideLayout1.xml（生成端默认挂载点），其次编号最小者。"""
    lays = sorted(n for n in names
                  if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml")
                  and "/_rels/" not in n)
    if not lays:
        return ""
    pref = "ppt/slideLayouts/slideLayout1.xml"
    return pref if pref in lays else lays[0]


def apply_template(src_pptx, template_pptx, dst_pptx=None):
    """用 template_pptx 的母版/版式/主题替换 src_pptx 的外观。

    返回 dst_pptx。任何异常都回退为「原样复制」，保证交付不中断
    （宁可用系统配色出一版，也不要让用户什么都拿不到）。
    """
    dst_pptx = dst_pptx or src_pptx
    try:
        with zipfile.ZipFile(template_pptx) as tz:
            tnames = tz.namelist()
            layout = _pick_layout(tnames)
            if not layout:
                raise ValueError("模板缺少 slideLayouts")
            with zipfile.ZipFile(src_pptx) as sz:
                # 1) 先写出我们自己的内容（slides / presentation / media）
                #    再叠加模板的外观件（theme / master / layout）
                merged = {}
                # 模板自带的媒体只搬「母版/版式真正引用到的」，避免把用户模板里
                # 成百上千张插图背上（实测会让 5KB 的生成稿膨胀到 15MB）
                need = _referenced_media(tz, tnames)
                for n in tnames:
                    # 外观子树整体搬过来，含 _rels 与被引用到的 media/字体
                    if n.startswith(_THEME_PREFIXES) or n == "[Content_Types].xml":
                        merged[n] = tz.read(n)
                    elif n in need:
                        merged[n] = tz.read(n)
                for n in sz.namelist():
                    # 外观件已在上面被模板覆盖；其余（slides/app/core）保留我们自己的
                    if n.startswith(_THEME_PREFIXES) or n == "[Content_Types].xml":
                        continue
                    merged[n] = sz.read(n)
                # 2) 重排 slide → layout 关系（模板里编号可能不是 slideLayout1）
                aim = "../" + os.path.basename(layout)
                for n in list(merged.keys()):
                    if re.match(r"ppt/slides/_rels/slide\d+\.xml\.rels$", n):
                        xml = merged[n].decode("utf-8")
                        xml = _RELIP_RE.sub(lambda m: m.group(0).replace(m.group(2), aim), xml)
                        merged[n] = xml.encode("utf-8")
                # 3) 合并 Content_Types：以"我们的"为底，补上模板声明的部件类型
                merged["[Content_Types].xml"] = _merge_content_types(
                    sz.read("[Content_Types].xml").decode("utf-8"),
                    tz.read("[Content_Types].xml").decode("utf-8"))
        with zipfile.ZipFile(dst_pptx, "w", zipfile.ZIP_DEFLATED) as out:
            for n, data in merged.items():
                out.writestr(n, data)
        return dst_pptx
    except Exception as e:
        # 换皮失败不阻断交付：直接用原始产物
        try:
            if os.path.abspath(dst_pptx) != os.path.abspath(src_pptx):
                shutil.copy2(src_pptx, dst_pptx)
        except Exception:
            return src_pptx
        return dst_pptx


_OVERRIDE_RE = re.compile(r'<Override\s+PartName="([^"]+)"\s+ContentType="([^"]+)"\s*/>')
_DEFAULT_RE = re.compile(r'<Default\s+Extension="([^"]+)"\s+ContentType="([^"]+)"\s*/>')


def _merge_content_types(ours, theirs):
    """合并两份 [Content_Types].xml：对外观类部件采用模板的声明，其余保留我们的。"""
    out_types = dict((p, c) for p, c in _OVERRIDE_RE.findall(ours))
    out_defs = dict((e, c) for e, c in _DEFAULT_RE.findall(ours))
    for p, c in _OVERRIDE_RE.findall(theirs):
        if p.startswith("/ppt/theme") or p.startswith("/ppt/slideMaster") \
                or p.startswith("/ppt/slideLayout"):
            out_types[p] = c
    for e, c in _DEFAULT_RE.findall(theirs):
        out_defs.setdefault(e, c)
    body = "".join('<Override PartName="%s" ContentType="%s"/>' % (p, c)
                   for p, c in out_types.items())
    defs = "".join('<Default Extension="%s" ContentType="%s"/>' % (e, c)
                   for e, c in out_defs.items())
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + defs + body + '</Types>')


if __name__ == "__main__":
    print(json.dumps(list_templates(), ensure_ascii=False, indent=2))
