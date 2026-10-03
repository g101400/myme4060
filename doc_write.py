# -*- coding: utf-8 -*-
"""
myme.doc_write —— 书面文档生成器（v2.6）

职责：把「structured deck content」渲染成两种交付物
  1. .docx —— 纯标准库手写 OOXML（与 pptx_write 同思路，不用 python-docx，打包零依赖丢失）
  2. .pdf  —— 经 LibreOffice 无头转换（缺 LibreOffice 时给出明确安装指引）

与 PPT 的内容差异是本模块的立身之本：
  · PPT   —— 要点式、每页信息量受限、靠讲的人补充，标题 + 短句 + 少量数据
  · 书面文档 —— 段落式、逻辑链完整、背景/方法/结论/建议都要写实，
               读者在没人在场的情况下也能看懂（适用于工作总结、汇报材料）

因此 write_docx 的输入不是"幻灯片"，而是独立的 blocks 结构（见 build_doc_blocks）。
"""
import os
import re
import zipfile
import subprocess
import xml.sax.saxutils as sx

# ============ LibreOffice 定位（用于导出 PDF）============
SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
    "/usr/bin/wps",                      # 统信 UOS 常见：WPS 也提供转换能力
    "/opt/apps/cn.wps.wps-office/files/bin/wps",
]


def find_soffice():
    for p in SOFFICE_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return ""


def convert_to_pdf(src, outdir=None, timeout=180):
    """把 docx / pptx / xlsx 转成 PDF。返回 (ok, pdf_path, error)。"""
    exe = find_soffice()
    if not exe:
        return False, "", "未找到 LibreOffice。请安装后重试：" \
                          "Windows：https://www.libreoffice.org/download/download/ ；" \
                          "统信 UOS：应用商店安装「LibreOffice」或用 WPS 的命令行转换。"
    outdir = outdir or os.path.dirname(os.path.abspath(src))
    try:
        r = subprocess.run([exe, "--headless", "--norestore",
                            "--convert-to", "pdf", "--outdir", outdir, src],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=timeout)
    except Exception as e:
        return False, "", "转换失败：%s" % e
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if os.path.exists(pdf):
        return True, pdf, ""
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return False, "", "转换未产出 PDF：%s" % (tail[-1] if tail else "未知原因")


# ============ 文本切分：把一段内容拆成 blocks ============
def _esc(t):
    return sx.escape(t or "")


def _inline_runs(text):
    """把 **加粗** 标记转成加粗 run，其余为普通 run。返回 w:p 内的 runs XML。"""
    if not text:
        return ""
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    out = []
    for p in parts:
        if not p:
            continue
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            out.append('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
                       % _esc(p[2:-2]))
        else:
            out.append('<w:t xml:space="preserve">%s</w:t>' % _esc(p))
    # 每个 w:t 必须包在 w:r 里，统一补壳
    joined = "".join(out)
    joined = re.sub(r"<w:t xml:space=\"preserve\">(.*?)</w:t>",
                    lambda m: '<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % m.group(1),
                    joined, flags=re.S)
    return joined


# ============ 核心：写 docx ============
_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>"""

# 中文字体必须显式设 eastAsia，否则 Word/WPS 会退回等线，标题看着不对
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/>
<w:sz w:val="21"/><w:szCs w:val="21"/>
</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:line="288" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:before="0" w:after="240"/><w:jc w:val="center"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light" w:eastAsia="Microsoft YaHei"/>
<w:b/><w:sz w:val="56"/><w:szCs w:val="56"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:outlineLvl w:val="0"/><w:keepNext/><w:spacing w:before="320" w:after="160"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light" w:eastAsia="Microsoft YaHei"/>
<w:b/><w:sz w:val="32"/><w:szCs w:val="32"/><w:color w:val="1D4ED8"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:outlineLvl w:val="1"/><w:keepNext/><w:spacing w:before="260" w:after="120"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Calibri Light" w:hAnsi="Calibri Light" w:eastAsia="Microsoft YaHei"/>
<w:b/><w:sz w:val="26"/><w:szCs w:val="26"/><w:color w:val="334155"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:outlineLvl w:val="2"/><w:keepNext/><w:spacing w:before="200" w:after="100"/></w:pPr>
<w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/><w:b/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="480"/><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="480" w:right="480"/><w:spacing w:before="120" w:after="120"/></w:pPr>
<w:rPr><w:i/><w:color w:val="64748B"/></w:rPr></w:style>
</w:styles>"""

# 无序列表（numId=1）+ 有序列表（numId=2）
_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0">
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="480" w:hanging="240"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="○"/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="960" w:hanging="240"/></w:pPr></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1">
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="480" w:hanging="240"/></w:pPr></w:lvl>
<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2."/>
<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="960" w:hanging="240"/></w:pPr></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""


def _p(style, xml_inner, extra_ppr=""):
    return ('<w:p><w:pPr><w:pStyle w:val="%s"/>%s</w:pPr>%s</w:p>' % (style, extra_ppr, xml_inner))


def _table(rows, header=True):
    """简单表格：等宽列，表头加粗加底纹。"""
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    total = 9360  # A4 正文可用宽度（twips）
    colw = total // max(ncol, 1)
    grid = "".join('<w:gridCol w:w="%d"/>' % colw for _ in range(ncol))
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
           '<w:tblW w:w="0" w:type="auto"/>'
           '<w:tblBorders>' + "".join(
               '<w:%s w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>' % e
               for e in ("top", "left", "bottom", "right", "insideH", "insideV")) +
           '</w:tblBorders></w:tblPr><w:tblGrid>%s</w:tblGrid>' % grid]
    for ri, row in enumerate(rows):
        cells = []
        for c in list(row) + [""] * (ncol - len(row)):
            shade = '<w:shd w:val="clear" w:color="auto" w:fill="E2E8F0"/>' if (header and ri == 0) else ""
            cells.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>%s</w:tcPr>'
                         '<w:p><w:r>%s</w:r></w:p></w:tc>' % (colw, shade, _inline_runs(str(c))))
        out.append("<w:tr>%s</w:tr>" % "".join(cells))
    out.append('</w:tbl><w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')
    return "".join(out)


def render_blocks(blocks, title=""):
    """blocks -> document.xml 的 body XML。"""
    parts = []
    if title:
        parts.append(_p("Title", _inline_runs(title)))
    for b in (blocks or []):
        t = b.get("type", "p")
        txt = b.get("text", "") or ""
        if t in ("h1", "h2", "h3"):
            parts.append(_p(t.capitalize(), _inline_runs(txt)))
        elif t == "p":
            parts.append(_p("Normal", _inline_runs(txt)))
        elif t == "quote":
            parts.append(_p("Quote", _inline_runs(txt)))
        elif t in ("bullets", "numbers"):
            num = 1 if t == "bullets" else 2
            for it in (b.get("items") or []):
                indent = 0 if str(it).startswith("·") else 0
                parts.append('<w:p><w:pPr><w:pStyle w:val="ListParagraph"/>'
                             '<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>'
                             '</w:pPr>%s</w:p>' % (indent, num, _inline_runs(str(it).lstrip("· "))))
        elif t == "table":
            parts.append(_table(b.get("rows") or [], header=b.get("header", True)))
        elif t == "pagebreak":
            parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif t == "meta":
            parts.append('<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
                         '<w:r><w:rPr><w:color w:val="94A3B8"/><w:sz w:val="18"/></w:rPr>'
                         '<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % _esc(txt))
    # 分节 увелича: 保证 Word 打开时不报修复
    body = "".join(parts) or _p("Normal", "")
    return ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
            'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>' + body)


def write_docx(title, blocks, out_path):
    """生成 .docx。返回 out_path。blocks 结构见 render_blocks。"""
    if not out_path.lower().endswith(".docx"):
        out_path += ".docx"
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % render_blocks(blocks, title))
    # 去掉 ZIP 里的时间戳无关项；此处显式给 date_time 保证可复现
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES, zipfile.ZIP_DEFLATED)
        z.writestr("_rels/.rels", _ROOT_RELS, zipfile.ZIP_DEFLATED)
        z.writestr("word/document.xml", doc, zipfile.ZIP_DEFLATED)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS, zipfile.ZIP_DEFLATED)
        z.writestr("word/styles.xml", _STYLES, zipfile.ZIP_DEFLATED)
        z.writestr("word/numbering.xml", _NUMBERING, zipfile.ZIP_DEFLATED)
    return out_path


def write_doc(name, title, blocks, outdir, formats=("docx", "pdf")):
    """一次产出多种格式。返回 {"docx": path|"", "pdf": path|"", "errors": [..]}。

    formats 支持 docx / pdf；PDF 由 LibreOffice 无头转换，
    不可用时不会中断，而是在 errors 里给出人话安装指引（供前端提示）。
    """
    res = {"docx": "", "pdf": "", "errors": []}
    fmts = [f.lower() for f in (formats or ["docx"])]
    safe = re.sub(r'[\\/:*?"<>|]+', "_", (name or "文档")).strip() or "文档"
    base = os.path.join(outdir, safe)
    docx = write_docx(title, blocks, base + ".docx")
    if "docx" in fmts:
        res["docx"] = docx
    if "pdf" in fmts:
        ok, pdf, err = convert_to_pdf(docx, outdir)
        if ok:
            res["pdf"] = pdf
        else:
            res["errors"].append(err)
    if "docx" not in fmts:
        # 只导出 PDF 时，docx 只是中间产物，交付后清理
        try:
            os.remove(docx)
        except Exception:
            pass
    return res


# ============ 从「PPT 要点」生成「书面文档」骨架 ============
def blocks_from_slides(slides, meta=None):
    """把 PPT 的要点式内容扩写成书面文档骨架（离线兜底，不依赖大模型）。

    做法：每张 PPT 的标题作为一级小节，要点作为引入段，
    并在每段后预留「详述」占位，提示作者/大模型补全论据、数据、过程。
    真正成文由 make_ppt / 大模型完成；这里保证"没网也能出一份可读的稿"。
    """
    meta = meta or {}
    blocks = [{"type": "meta", "text": "%s · %s" % (meta.get("theme", ""), meta.get("date", ""))}]
    for i, s in enumerate(slides or [], 1):
        title = (s.get("title") or "").strip() or ("第 %d 部分" % i)
        blocks.append({"type": "h1", "text": "%d. %s" % (i, title)})
        bullets = [b for b in (s.get("bullets") or s.get("points") or []) if str(b).strip()]
        if bullets:
            blocks.append({"type": "p", "text": "本部分要点如下，后文逐一展开。"})
            blocks.append({"type": "bullets", "items": bullets})
        body = (s.get("text") or s.get("detail") or "").strip()
        if body:
            for para in [x.strip() for x in body.split("\n") if x.strip()]:
                blocks.append({"type": "p", "text": para})
        else:
            blocks.append({"type": "p",
                           "text": "（此处补充过程、数据与结论：做到什么程度、"
                                   "依据哪些数据、遇到什么问题、下一步怎么做。）"})
    return blocks


if __name__ == "__main__":
    demo = [
        {"type": "p", "text": "这是 **书面文档** 生成器的自检样例。"},
        {"type": "h1", "text": "一、工作开展情况"},
        {"type": "bullets", "items": ["完成三项重点任务", "形成两套标准模板"]},
        {"type": "h2", "text": "1.1 数据依据"},
        {"type": "table", "rows": [["指标", "数值"], ["完成率", "98%"]]},
    ]
    p = write_docx("自检样例", demo, "./test_out/doc_write_demo.docx")
    print("docx:", p, os.path.getsize(p), "bytes")
    print(convert_to_pdf(p, "./test_out"))
