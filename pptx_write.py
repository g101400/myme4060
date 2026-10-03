# -*- coding: utf-8 -*-
"""
myme.pptx_write — 纯标准库生成 .pptx（零第三方依赖，无需 python-pptx）

输入：slides = [{"title": str, "bullets": [str, ...], "image": 可选 本地图片绝对路径}, ...]
输出：合法 OOXML .pptx 文件，可被 PowerPoint / WPS / 本应用 pptx_parse 解析。

用于「制作 PPT」功能：大模型生成结构化大纲 -> 本函数落盘 .pptx（可下载 / 一键导入准备工作）。
"""
import os
import zipfile
from xml.sax.saxutils import escape

EMU = 914400  # 每英寸 EMU 数
SLIDE_W = 10 * EMU
SLIDE_H = 7.5 * EMU

NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# ⚠ OPC 规范里"包根关系"（_rels/.rels）属于 /package/2006/relationships 命名空间，
#   与包内部件关系（officeDocument/2006/relationships）不是同一个。
#   两者混用会让解析器认不出 Relationship 元素：
#   python-pptx 报 'lxml.etree._Element' object has no attribute 'relationship_lst'，
#   LibreOffice 直接报 "source file could not be loaded"（连带 PPT→PDF 导出失败）。
NS_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"

REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_SLIDE = REL_OFFICE + "/slide"
REL_SLIDELAYOUT = REL_OFFICE + "/slideLayout"
REL_SLIDEMASTER = REL_OFFICE + "/slideMaster"
REL_THEME = REL_OFFICE + "/theme"
REL_IMAGE = REL_OFFICE + "/image"


def _run(text, sz=1800, b=False):
    battr = ' b="1"' if b else ""
    return (f'<a:r><a:rPr lang="zh-CN" sz="{sz}"{battr} dirty="0"/>'
            f'<a:t>{escape(text)}</a:t></a:r>')


def _title_shape(text):
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="274320"/>'
        '<a:ext cx="8229600" cy="900000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="ctr"/>'
        '<a:lstStyle/><a:p>' + _run(text, sz=3200, b=True) + '</a:p></p:txBody></p:sp>'
    )


def _body_shape(lines):
    paras = []
    for ln in (lines or []):
        paras.append(
            '<a:p><a:pPr marL="285750" indent="-285750">'
            '<a:buFont typeface="Arial"/><a:buChar char="•"/></a:pPr>'
            + _run(ln, sz=1800) + '</a:p>'
        )
    if not paras:
        paras.append('<a:p/>')
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/>'
        '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="457200" y="1300000"/>'
        '<a:ext cx="6000000" cy="5300000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
        '<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"/>'
        '<a:lstStyle/>' + "".join(paras) + '</p:txBody></p:sp>'
    )


def _pic_shape(rel_id):
    return (
        '<p:pic><p:nvPicPr><p:cNvPr id="4" name="Picture"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
        '<p:blipFill><a:blip r:embed="' + rel_id + '"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        '<p:spPr><a:xfrm><a:off x="6600000" y="1300000"/>'
        '<a:ext cx="2400000" cy="5300000"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
    )


def _slide_xml(title, bullets, image_rel=None):
    shapes = [_title_shape(title), _body_shape(bullets)]
    if image_rel:
        shapes.append(_pic_shape(image_rel))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '" xmlns:p="' + NS_P + '">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes) +
        '</p:spTree></p:cSld></p:sld>'
    )


def _slide_rels(image_rel=None):
    out = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="' + NS_PKG_RELS + '">'
        '<Relationship Id="rId1" Type="' + REL_SLIDELAYOUT + '" Target="../slideLayouts/slideLayout1.xml"/>'
    )
    if image_rel:
        out += ('<Relationship Id="rId2" Type="' + REL_IMAGE + '" Target="../media/' + image_rel + '"/>')
    out += '</Relationships>'
    return out


_THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<a:theme xmlns:a="' + NS_A + '" name="Office Theme">'
    '<a:themeElements>'
    '<a:clrScheme name="Office">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2>'
    '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
    '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
    '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
    '<a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>'
    '<a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
    '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
    '</a:clrScheme>'
    '<a:fontScheme name="Office">'
    '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
    '</a:fontScheme>'
    '<a:fmtScheme name="Office">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr">'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    '</a:fmtScheme>'
    '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'
)

_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldMaster xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '" xmlns:p="' + NS_P + '">'
    '<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>'
    '<a:effectLst/></p:bgPr></p:bg><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder 1"/>'
    '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
    '<p:nvPr><p:ph type="title" sz="quarter" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
    '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Text Placeholder 2"/>'
    '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
    '<p:nvPr><p:ph type="body" idx="2"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
    '</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="3200"/></a:lvl1pPr></p:titleStyle>'
    '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
    '<p:otherStyle/></p:txStyles><p:extLst/></p:sldMaster>'
)

_MASTER_RELS = (
    # ⚠ 路径是相对「当前 part 所在目录」解析的：本件位于 ppt/slideMasters/，
    #   所以必须用 ../ 回到 ppt/ 再进 slideLayouts / theme。写成同级会指向
    #   ppt/slideMasters/slideLayouts/... —— 母版找不到版式与主题，
    #   PowerPoint 会尝试修复，而 LibreOffice 直接判定"文件无法打开"，
    #   连带 PPT→PDF 导出失败。
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="' + NS_PKG_RELS + '">'
    '<Relationship Id="rId1" Type="' + REL_SLIDELAYOUT + '" Target="../slideLayouts/slideLayout1.xml"/>'
    '<Relationship Id="rId2" Type="' + REL_THEME + '" Target="../theme/theme1.xml"/>'
    '</Relationships>'
)

_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldLayout xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '" xmlns:p="' + NS_P + '" '
    'type="blank" preserve="1">'
    '<p:cSld name="Blank"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '</p:spTree></p:cSld>'
    '<p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" '
    'accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
    'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/></p:clrMapOvr>'
    '</p:sldLayout>'
)

_LAYOUT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="' + NS_PKG_RELS + '">'
    '<Relationship Id="rId1" Type="' + REL_SLIDEMASTER + '" Target="../slideMasters/slideMaster1.xml"/>'
    '</Relationships>'
)


def _content_types(n_slides, media):
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Default Extension="png" ContentType="image/png"/>',
        '<Default Extension="jpg" ContentType="image/jpeg"/>',
        '<Default Extension="jpeg" ContentType="image/jpeg"/>',
        '<Default Extension="gif" ContentType="image/gif"/>',
        '<Default Extension="bmp" ContentType="image/bmp"/>',
        '<Default Extension="webp" ContentType="image/webp"/>',
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]
    for i in range(1, n_slides + 1):
        parts.append('<Override PartName="/ppt/slides/slide%d.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i)
    for name, ctype in media:
        parts.append('<Override PartName="/ppt/media/%s" ContentType="%s"/>' % (name, ctype))
    parts.append('</Types>')
    return "".join(parts)


def _presentation_xml(n_slides):
    sld_ids = []
    for i in range(1, n_slides + 1):
        sld_ids.append('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, 1 + i))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '" xmlns:p="' + NS_P + '" saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:sldIdLst>' + "".join(sld_ids) + '</p:sldIdLst>'
        '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
        % (SLIDE_W, SLIDE_H)
    )


def _presentation_rels(n_slides):
    rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="' + NS_PKG_RELS + '">',
        '<Relationship Id="rId1" Type="' + REL_SLIDEMASTER + '" Target="slideMasters/slideMaster1.xml"/>',
    ]
    for i in range(1, n_slides + 1):
        rels.append('<Relationship Id="rId%d" Type="%s" Target="slides/slide%d.xml"/>' % (1 + i, REL_SLIDE, i))
    rels.append('</Relationships>')
    return "".join(rels)


def _root_rels():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="' + NS_PKG_RELS + '">'
        '<Relationship Id="rId1" Type="' + REL_OFFICE + '/officeDocument" Target="ppt/presentation.xml"/>'
        '</Relationships>'
    )


def _media_ctype(ext):
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    }.get(ext.lower(), "image/png")


# ==================== 配色主题 + 版式 + 图表（纯标准库绘制）====================
# 三套配色：蓝白商务 / 深色科技 / 简约灰
THEMES = {
    "blue": {"bg": "FFFFFF", "title": "1F3864", "accent": "2E75B6",
             "text": "333333", "soft": "DEEBF7"},
    "dark": {"bg": "1F2937", "title": "FFFFFF", "accent": "38BDF8",
             "text": "E5E7EB", "soft": "374151"},
    "gray": {"bg": "F5F5F5", "title": "374151", "accent": "6B7280",
             "text": "4B5563", "soft": "E5E7EB"},
}


def _solid(color):
    return '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % color


def _run_c(text, sz=1800, b=False, color=None):
    """带颜色的文本 run（深色主题下避免黑字压黑底）。"""
    fill = _solid(color) if color else ''
    battr = ' b="1"' if b else ''
    return ('<a:r><a:rPr lang="zh-CN" sz="%d"%s dirty="0">%s</a:rPr>'
            '<a:t>%s</a:t></a:r>') % (sz, battr, fill, escape(text))


def _shape(sid, name, x, y, cx, cy, prst="rect", fill=None, adj=None,
           lines=None, sz=1400, color=None, bold=False, align="ctr",
           anchor="ctr", line=None, bullet=False, rot=0):
    """通用形状：可带文本（多行）、填充、描边、旋转。用于版式与图表绘制。"""
    geom = '<a:prstGeom prst="%s">%s</a:prstGeom>' % (prst, adj or '<a:avLst/>')
    fillxml = _solid(fill) if fill else '<a:noFill/>'
    if line:
        lnex = '<a:ln w="12700">%s</a:ln>' % _solid(line)
    else:
        lnex = '<a:ln><a:noFill/></a:ln>'
    paras = ''
    if lines is not None:
        ls = lines if isinstance(lines, (list, tuple)) else [lines]
        for ln in ls:
            bu = ('<a:buFont typeface="Arial"/><a:buChar char="•"/>') if bullet else ''
            paras += ('<a:p><a:pPr algn="%s" marL="285750" indent="-285750">%s</a:pPr>%s</a:p>'
                      % (align, bu, _run_c(ln, sz=sz, b=bold, color=color)))
    body = ''
    if lines is not None:
        body = ('<p:txBody><a:bodyPr wrap="square" lIns="45720" tIns="22860" rIns="45720" '
                'bIns="22860" anchor="%s"/><a:lstStyle/>%s</p:txBody>') % (anchor, paras or '<a:p/>')
    rotattr = ' rot="%d"' % rot if rot else ''
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
            '<p:spPr><a:xfrm%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '%s%s%s</p:spPr>%s</p:sp>') % (sid, escape(name), rotattr, x, y, cx, cy,
                                           geom, fillxml, lnex, body)


def _chart_shapes(sid, chart, th, x, y, cx, cy):
    """画简易图表（bar / line / pie），返回 (形状XML列表, 下一个可用id)。

    没有可靠数据（type=none 或 labels/values 不匹配）时不画任何图形——不硬造图表。
    """
    import math
    chart = chart or {}
    ctype = str(chart.get("type") or "none").lower()
    labels = [str(t) for t in (chart.get("labels") or [])]
    values = []
    for v in (chart.get("values") or []):
        try:
            values.append(float(v))
        except Exception:
            values.append(0.0)
    unit = str(chart.get("unit") or "")
    shapes = []
    if ctype == "none" or not labels or len(labels) != len(values) or not values:
        return shapes, sid
    n = len(values)
    mx = max(values) or 1.0
    mn = min(values + [0.0])
    rng = (mx - mn) or 1.0

    if ctype == "bar":
        plot_h = int(cy * 0.70)
        base_y = y + plot_h
        bw = max(120000, int(cx / (n * 1.8)))
        gap = max(60000, int((cx - bw * n) / (n + 1)))
        for i, v in enumerate(values):
            h = max(60000, int(plot_h * (v - mn) / rng))
            bx = x + gap + i * (bw + gap)
            by = base_y - h
            shapes.append(_shape(sid, "Bar%d" % i, bx, by, bw, h, "rect", th["accent"])); sid += 1
            shapes.append(_shape(sid, "V%d" % i, bx - bw, by - 280000, bw * 3, 260000, "rect", None,
                                 lines=["%g%s" % (v, unit)], sz=1000, color=th["text"])); sid += 1
            shapes.append(_shape(sid, "L%d" % i, bx - bw, base_y + 40000, bw * 3, 280000, "rect", None,
                                 lines=[labels[i]], sz=1000, color=th["text"])); sid += 1
        shapes.append(_shape(sid, "Axis", x, base_y, cx, 30000, "rect", th["text"])); sid += 1

    elif ctype == "line":
        plot_h = int(cy * 0.70)
        base_y = y + plot_h
        step = int(cx / max(1, n - 1)) if n > 1 else 0
        pts = []
        for i, v in enumerate(values):
            px = x + (i * step if n > 1 else cx // 2)
            py = base_y - max(60000, int(plot_h * (v - mn) / rng))
            pts.append((px, py))
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            dx, dy = x2 - x1, y2 - y1
            length = max(60000, int((dx * dx + dy * dy) ** 0.5))
            ang = int(math.degrees(math.atan2(dy, dx)) * 60000)
            mpx, mpy = (x1 + x2) // 2, (y1 + y2) // 2
            shapes.append(_shape(sid, "Seg%d" % i, mpx - length // 2, mpy - 25000,
                                 length, 50000, "rect", th["accent"], rot=ang)); sid += 1
        for i, (px, py) in enumerate(pts):
            shapes.append(_shape(sid, "D%d" % i, px - 90000, py - 90000, 180000, 180000,
                                 "ellipse", th["accent"], line=th["accent"])); sid += 1
            shapes.append(_shape(sid, "LV%d" % i, px - 400000, base_y + 40000, 800000, 280000,
                                 "rect", None, lines=[labels[i]], sz=900, color=th["text"])); sid += 1
        shapes.append(_shape(sid, "Axis", x, base_y, cx, 30000, "rect", th["text"])); sid += 1

    elif ctype == "pie":
        # 用 blockArc 扇形拼出饼图（adj1 起始角 / adj2 扫过角，单位 1/60000 度）
        total = sum(values) or 1.0
        d = min(cx, int(cy * 0.95))
        palette = [th["accent"], "70AD47", "ED7D31", "FFC000", "5B9BD5", "A5A5A5"]
        start = 0.0
        for i, v in enumerate(values):
            sweep = v / total
            adj = ('<a:avLst><a:gd name="adj1" fmla="val %d"/>'
                   '<a:gd name="adj2" fmla="val %d"/></a:avLst>'
                   % (int(start * 21600000), int(sweep * 21600000)))
            shapes.append(_shape(sid, "P%d" % i, x, y, d, d, "blockArc",
                                 palette[i % len(palette)], adj=adj)); sid += 1
            start += sweep
        for i, v in enumerate(values):
            ly = y + 300000 * i
            shapes.append(_shape(sid, "G%d" % i, x + d + 150000, ly, 150000, 140000,
                                 "rect", palette[i % len(palette)])); sid += 1
            shapes.append(_shape(sid, "GT%d" % i, x + d + 340000, ly,
                                 max(300000, cx - d - 360000), 160000, "rect", None,
                                 lines=["%s  %g%s" % (labels[i], v, unit)],
                                 sz=900, color=th["text"], align="l")); sid += 1
    return shapes, sid


def _slide_xml_ex(sl, theme="blue", image_rel=None):
    """按 layout 渲染一页：cover / bullets / compare / flow / data（含图表）。"""
    th = THEMES.get(theme, THEMES["blue"])
    layout = str(sl.get("layout") or "bullets").lower()
    title = str(sl.get("title") or "")
    bullets = [str(b) for b in (sl.get("bullets") or [])]
    chart = sl.get("chart") or {"type": "none"}
    icons = [str(i) for i in (sl.get("icons") or [])][:3]
    shapes = [_shape(2, "Bg", 0, 0, SLIDE_W, SLIDE_H, "rect", th["bg"])]

    def _tt(sid):
        return _shape(sid, "Title", 457200, 274320, 8229600, 900000, "rect", None,
                      lines=[title], sz=3000, color=th["title"], bold=True,
                      align="l", anchor="ctr")

    if layout == "cover":
        shapes.append(_shape(3, "Bar", 700000, 2050000, 1300000, 110000, "rect", th["accent"]))
        shapes.append(_shape(4, "Title", 700000, 2350000, 7800000, 1700000, "rect", None,
                             lines=[title], sz=4000, color=th["title"], bold=True,
                             align="l", anchor="t"))
        if bullets:
            shapes.append(_shape(5, "Sub", 700000, 4200000, 7800000, 1500000, "rect", None,
                                 lines=bullets, sz=1800, color=th["text"], align="l",
                                 anchor="t", bullet=True))
    elif layout == "compare":
        shapes.append(_tt(3))
        half = (len(bullets) + 1) // 2 or 1
        shapes.append(_shape(11, "Left", 500000, 1700000, 4200000, 4000000, "rect", th["soft"],
                             lines=bullets[:half] or ["（左：现状）"], sz=1500,
                             color=th["text"], align="l", anchor="t", bullet=True))
        shapes.append(_shape(12, "Right", 4800000, 1700000, 4200000, 4000000, "rect", th["soft"],
                             lines=bullets[half:] or ["（右：目标）"], sz=1500,
                             color=th["text"], align="l", anchor="t", bullet=True))
    elif layout == "flow":
        shapes.append(_tt(3))
        for i, b in enumerate(bullets[:5], 1):
            bx = 400000 + (i - 1) * 1900000
            shapes.append(_shape(20 + i, "S%d" % i, bx, 2300000, 1750000, 1300000,
                                 "roundRect", th["soft"], lines=["%d. %s" % (i, b)],
                                 sz=1250, color=th["text"], align="ctr", line=th["accent"]))
            if i < min(len(bullets), 5):
                shapes.append(_shape(40 + i, "A%d" % i, bx + 1780000, 2850000,
                                     150000, 80000, "rect", th["accent"]))
    elif layout == "data" and str(chart.get("type") or "none") != "none":
        shapes.append(_tt(3))
        cs, _sid = _chart_shapes(50, chart, th, 700000, 1800000, 5200000, 4300000)
        shapes += cs
        if bullets:
            shapes.append(_shape(90, "Notes", 6100000, 1800000, 3300000, 4300000, "rect",
                                 th["soft"], lines=bullets, sz=1200, color=th["text"],
                                 align="l", anchor="t", bullet=True))
    else:  # bullets（默认）
        shapes.append(_tt(3))
        shapes.append(_shape(6, "Body", 457200, 1400000, 8229600, 4900000, "rect", None,
                             lines=bullets or ["（本页待补充）"], sz=1700,
                             color=th["text"], align="l", anchor="t", bullet=True))

    # 图标关键词（右下角小标签）
    for i, ic in enumerate(icons):
        shapes.append(_shape(60 + i, "I%d" % i, 500000 + i * 1450000, 6650000,
                             1300000, 420000, "roundRect", th["soft"], lines=[ic],
                             sz=1100, color=th["accent"]))

    if image_rel:
        shapes.append(_pic_shape(image_rel))

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '" xmlns:p="' + NS_P + '">'
        '<p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes) +
        '</p:spTree></p:cSld></p:sld>'
    )


def write_pptx(out_path, slides, theme="blue"):
    """把结构化大纲写入 .pptx 文件。

    slides: list[{title, bullets:[...], layout: cover/bullets/compare/flow/data,
                  icons:[...], chart:{type,unit,labels,values}, image: 可选路径}]
    theme : blue（蓝白商务）/ dark（深色科技）/ gray（简约灰）
    """
    n = len(slides)
    # 收集图片（去重、分配 media 名），并建立 每页 image_rel
    media = []          # [(name, ctype)]
    seen = {}
    page_image_rel = []
    for sl in slides:
        img = sl.get("image")
        if img and os.path.exists(img):
            ext = os.path.splitext(img)[1].lower() or ".png"
            if img not in seen:
                name = "image%d%s" % (len(media) + 1, ext)
                seen[img] = name
                media.append((name, _media_ctype(ext)))
            page_image_rel.append(seen[img])
        else:
            page_image_rel.append(None)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _content_types(n, media))
        z.writestr("_rels/.rels", _root_rels())
        z.writestr("ppt/presentation.xml", _presentation_xml(n))
        z.writestr("ppt/_rels/presentation.xml.rels", _presentation_rels(n))
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _MASTER_RELS)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _LAYOUT_RELS)
        z.writestr("ppt/theme/theme1.xml", _THEME)
        for i, sl in enumerate(slides):
            z.writestr("ppt/slides/slide%d.xml" % (i + 1),
                       _slide_xml_ex(sl, theme=theme, image_rel=page_image_rel[i]))
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % (i + 1), _slide_rels(page_image_rel[i]))
        for name, _ct in media:
            src = [k for k, v in seen.items() if v == name][0]
            with open(src, "rb") as f:
                z.writestr("ppt/media/" + name, f.read())
    return out_path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "test_out.pptx"
    write_pptx(out, [
        {"title": "封面：项目汇报", "bullets": ["背景与目标", "整体方案", "关键成果"]},
        {"title": "第一章 背景", "bullets": ["行业现状", "存在问题", "建设必要性"]},
        {"title": "第二章 方案", "bullets": ["技术路线", "实施步骤"]},
    ])
    print("written:", out)
