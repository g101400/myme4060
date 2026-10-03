# -*- coding: utf-8 -*-
"""
myme.doc_parse — 多格式文档抽取（纯标准库 + LibreOffice 兜底）

支持：
  .txt .md            直接读
  .docx               OOXML：word/document.xml 的 <w:t>
  .pptx / .ppt        复用 pptx_parse（按页，含标题/正文/备注/图片）
  .xlsx               OOXML：sharedStrings.xml + worksheets/sheetN.xml
  .doc .xls .pdf .rtf 降级：LibreOffice headless 转 txt 后读取

统一输出：
  {"kind": "slides"|"text", "slides": [{idx,title,text,notes,images}], "text": "...", "pages": n}
"""
import os
import re
import zipfile
import subprocess
import xml.etree.ElementTree as ET

# LibreOffice 可执行文件路径（兜底转换用）
SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "soffice",
]

SLIDE_EXT = (".pptx", ".ppt")
TEXT_EXT = (".txt", ".md", ".markdown")
DOCX_EXT = (".docx",)
XLSX_EXT = (".xlsx",)
SOFFICE_EXT = (".doc", ".xls", ".pdf", ".rtf", ".odt", ".ods", ".odp")

ALL_EXT = SLIDE_EXT + TEXT_EXT + DOCX_EXT + XLSX_EXT + SOFFICE_EXT


def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def find_soffice():
    for c in SOFFICE_CANDIDATES:
        if os.path.sep in c or "/" in c:
            if os.path.exists(c):
                return c
        else:
            from shutil import which
            w = which(c)
            if w:
                return w
    return None


# ---------------- .txt / .md ----------------
def _read_text(path):
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "ignore")


# ---------------- .docx ----------------
def _extract_docx(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n == "word/document.xml"]
        if not names:
            return ""
        root = ET.fromstring(z.read(names[0]))
    parts = []
    # <w:p> 段落为换行单位，段内所有 <w:t> 拼接
    for para in root.iter():
        if _local(para.tag) != "p":
            continue
        buf = []
        for el in para.iter():
            if _local(el.tag) == "t" and el.text:
                buf.append(el.text)
            elif _local(el.tag) == "tab":
                buf.append("\t")
        line = "".join(buf).strip()
        if line:
            parts.append(line)
    return "\n".join(parts)


# ---------------- .xlsx ----------------
def _extract_xlsx(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.iter():
                if _local(si.tag) != "si":
                    continue
                buf = []
                for el in si.iter():
                    if _local(el.tag) == "t" and el.text:
                        buf.append(el.text)
                shared.append("".join(buf))
        sheets = sorted(n for n in names
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for sn in sheets:
            sroot = ET.fromstring(z.read(sn))
            rows = {}
            for c in sroot.iter():
                if _local(c.tag) != "c":
                    continue
                ref = c.get("r") or ""
                m = re.match(r"([A-Z]+)(\d+)$", ref)
                if not m:
                    continue
                ci = 0
                for ch in m.group(1):
                    ci = ci * 26 + (ord(ch) - 64)
                rn = int(m.group(2))
                t = c.get("t")
                val = ""
                for el in c.iter():
                    if _local(el.tag) == "v" and el.text:
                        val = el.text
                    elif _local(el.tag) == "is":
                        val = "".join(x.text or "" for x in el.iter()
                                      if _local(x.tag) == "t")
                if t == "s" and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                if val.strip():
                    rows.setdefault(rn, {})[ci] = val.strip()
            for rn in sorted(rows):
                cells = rows[rn]
                out.append(" | ".join(cells[k] for k in sorted(cells)))
        return "\n".join(out)


# ---------------- .pdf ----------------
# PDF 文本抽取依赖 pypdf（纯 Python，无二进制依赖）。
# 注意：LibreOffice 走不通——PDF 被 Draw 导入后没有文本导出过滤器（实测报 no export filter）。
def _extract_pdf(path):
    try:
        import pypdf
    except ImportError:
        raise RuntimeError(
            "解析 PDF 需要 pypdf。请在系统 python 执行：python -m pip install pypdf")
    reader = pypdf.PdfReader(path)
    parts = []
    for pg in reader.pages:
        try:
            t = pg.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(t.strip())
    return "\n".join(parts)


# ---------------- LibreOffice 兜底 ----------------
def _extract_via_soffice(path):
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "该文件格式需要 LibreOffice 转换（.doc/.xls/.pdf/.rtf），但未找到 soffice。")
    outdir = os.path.join(os.path.dirname(os.path.abspath(path)), "_soffice_tmp")
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(
        [soffice, "--headless", "--norestore", "--convert-to", "txt:Text",
         "--outdir", outdir, os.path.abspath(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    base = os.path.splitext(os.path.basename(path))[0]
    txt = os.path.join(outdir, base + ".txt")
    if not os.path.exists(txt):
        cands = [f for f in os.listdir(outdir) if f.lower().endswith(".txt")]
        if not cands:
            raise RuntimeError(f"LibreOffice 转换失败：{os.path.basename(path)}")
        txt = os.path.join(outdir, sorted(cands)[0])
    return _read_text(txt)


# ---------------- 统一入口 ----------------
def extract(path):
    """返回 {"kind","slides","text","pages"}。kind=slides 表示可按页展示。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in SLIDE_EXT:
        import pptx_parse
        slides = pptx_parse.parse_pptx(path)
        return {"kind": "slides", "slides": slides, "text": "", "pages": len(slides)}
    if ext in TEXT_EXT:
        return {"kind": "text", "slides": [], "text": _read_text(path), "pages": 0}
    if ext in DOCX_EXT:
        return {"kind": "text", "slides": [], "text": _extract_docx(path), "pages": 0}
    if ext in XLSX_EXT:
        return {"kind": "text", "slides": [], "text": _extract_xlsx(path), "pages": 0}
    if ext == ".pdf":
        return {"kind": "text", "slides": [], "text": _extract_pdf(path), "pages": 0}
    if ext in SOFFICE_EXT:
        return {"kind": "text", "slides": [], "text": _extract_via_soffice(path), "pages": 0}
    raise ValueError(f"不支持的文件类型：{ext}")


def supported_accept():
    return ",".join(ALL_EXT)


if __name__ == "__main__":
    import sys, json
    p = sys.argv[1]
    r = extract(p)
    print("kind =", r["kind"], " pages =", r["pages"], " chars =", len(r["text"]))
    if r["kind"] == "slides":
        print("slide[0]:", json.dumps(r["slides"][0], ensure_ascii=False)[:200])
    else:
        print(r["text"][:300])
