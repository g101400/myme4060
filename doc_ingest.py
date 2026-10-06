# -*- coding: utf-8 -*-
"""
myme.doc_ingest — 本地知识库文档摄取（纯标准库，零依赖）
职责：
  1) 抽取：.txt / .md / .docx（docx 用 zip+xml 解析，同 pptx 思路）/ .pptx（复用 pptx_parse）
  2) 清洗：去 HTML 标签、去噪、脱敏（身份证/手机号/邮箱）
  3) 元数据：文件名归一化为 【分类】标题｜来源｜日期
  4) 指纹：文件 sha256，供增量入库时跳过重算
设计原则：与主项目一致——主逻辑跑系统 python，不碰嵌式 python；不依赖任何 pip 包。
"""
import os
import re
import json
import hashlib
import zipfile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
X_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# 目录批量导入时忽略的目录（跟 git 的 sense 一致）
SKIP_DIR_NAMES = {"node_modules", "__pycache__", ".git", ".svn", ".idea", ".venv", "venv"}
# ⚠ 运行期重依赖目录必须排除：LivePortrait 自带 700MB 源码/权重/示例文档，
#   ts_deps 又带一份 transformers。批量导入时不排除，扫进来上百个文件（还都是
#   changelog/README 这类噪声），把真文档挤到后头，还会把 KB 体积撑大一个量级。
SKIP_DIR_NAMES |= {"LivePortrait", "ts_deps", "dist", "build", "dist_pkg",
                   "android-player", "ios", "_vendor", "_scratch"}


# ============================================================
# 一·二、扩展格式抽取（对齐 WeKnora 的文档格式清单）
#   原则仍是「零依赖优先」：能纯标准库搞定的绝不引三方；
#   引三方的（openpyxl / bs4 / PyPDF2）一律 try-import + 回退，缺了也能跑，只是格式少一种。
# ============================================================
def _extract_pdf(path):
    """PDF：优先 PyPDF2，缺则给明确提示（不静默返回空——空文本会被当成「无内容」跳过）。"""
    try:
        import PyPDF2
    except ImportError:
        raise ValueError("读 PDF 需要 PyPDF2（pip install PyPDF2），当前环境没有")
    rd = PyPDF2.PdfReader(path)
    parts = []
    for i, pg in enumerate(rd.pages, 1):
        try:
            t = pg.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append("【PDF第%d页】\n%s" % (i, t))
    return "\n\n".join(parts)


def _extract_xlsx(path):
    """Excel：openpyxl 优先；退回 zip+XML（与 docx 同思路），保证零依赖也能读。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        parts = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("【工作表 %s】\n%s" % (ws.title, "\n".join(rows)))
        wb.close()
        return "\n\n".join(parts)
    except ImportError:
        pass
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{{{X_NS}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{X_NS}}}t")))
        sheet = "xl/worksheets/sheet1.xml"
        if sheet not in z.namelist():
            cands = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet")]
            if not cands:
                return ""
            sheet = sorted(cands)[0]
        root = ET.fromstring(z.read(sheet))
        parts = []
        for row in root.iter(f"{{{X_NS}}}row"):
            cells = []
            for c in row.iter(f"{{{X_NS}}}c"):
                v = c.find(f"{{{X_NS}}}v")
                if v is None or v.text is None:
                    continue
                if c.get("t") == "s":
                    idx = int(v.text)
                    cells.append(shared[idx] if idx < len(shared) else "")
                else:
                    cells.append(v.text)
            if any(x.strip() for x in cells):
                parts.append(" | ".join(cells))
        return "\n".join(parts)


def _extract_csv(path):
    import csv as _csv
    with open(path, encoding="utf-8-sig", errors="ignore", newline="") as f:
        rows = list(_csv.reader(f))
    return "\n".join(" | ".join(c.strip() for c in r if c.strip()) for r in rows if r)


def _extract_html(path):
    """HTML：bs4 优先；没有就用已有的白名单去标签（doc_ingest 里 _tag_re 那套）。"""
    raw = open(path, encoding="utf-8", errors="ignore").read()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, "html.parser")
        for bad in soup(["script", "style", "noscript", "svg"]):
            bad.decompose()
        txt = soup.get_text("\n")
    except ImportError:
        txt = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", raw)
        txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
        txt = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", txt)
        txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _extract_json(path):
    """JSON：扁平化成「键: 值」文本，保留嵌套结构的可读性（深层嵌套展平成路径）。"""
    with open(path, encoding="utf-8", errors="ignore") as f:
        d = json.load(f)

    def walk(obj, prefix=""):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, (dict, list)):
                    out.extend(walk(v, key))
                else:
                    out.append("%s: %s" % (key, v))
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:200]):          # 大列表截断，别把 KB 撑爆
                if isinstance(v, (dict, list)):
                    out.extend(walk(v, "%s[%d]" % (prefix, i)))
                else:
                    out.append("%s[%d]: %s" % (prefix, i, v))
        elif prefix:
            out.append("%s: %s" % (prefix, obj))
        return out

    return "\n".join(walk(d))


def _extract_epub(path):
    """EPUB：本质是 zip，读 container 指到的 opf，再按 spine 顺序拼章节。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        opf = ""
        if "META-INF/container.xml" in names:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            for f in root.iter():
                if "full-path" in (f.attrib.get("full-path", "").lower() if False else ""):
                    pass
            m = re.findall(r'full-path="([^"]+)"', z.read("META-INF/container.xml").decode("utf-8", "ignore"))
            opf = m[0] if m else ""
        if not opf or opf not in names:
            cands = [n for n in names if n.endswith(".opf")]
            opf = cands[0] if cands else ""
        if not opf:
            return ""
        base = os.path.dirname(opf)
        base = base + "/" if base else ""
        xhtml = re.findall(r'href="([^"]+)"', z.read(opf).decode("utf-8", "ignore"))
        parts = []
        for href in xhtml:
            if href.startswith("http") or href.startswith("#"):
                continue
            rel = href
            while rel.startswith("../"):            # 允许 ../../ 上跳
                rel = rel[3:]
            cand = os.path.normpath(base + rel).replace("\\", "/")
            if cand not in names:
                cand = os.path.normpath(base + rel.replace("../", "")).replace("\\", "/")
            if cand not in names:
                continue
            raw = z.read(cand).decode("utf-8", "ignore")
            txt = re.sub(r"(?is)^<\?xml.*?\?>", "", raw)
            txt = re.sub(r"(?si)<(script|style).*?</\1>", " ", txt)
            txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
            txt = re.sub(r"<[^>]+>", "\n", txt)
            txt = re.sub(r"&#?\w+;", " ", txt)
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
            if txt:
                parts.append(txt)
        return "\n\n".join(parts)


def _extract_image(path):
    """图片：只做「占位 + 尺寸/ocr 提示」，不硬塞 OCR 引擎依赖。

    WeKnora 的图片 OCR 需要额外装 tesseract；本机没有，故降级为：
    有 OCR 二进制就跑，没有就按空文本返回（上层会跳过该文件，不会崩）。
    """
    tesseract = ""
    for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.isfile(c):
            tesseract = c
            break
    if not tesseract:
        from shutil import which
        tesseract = which("tesseract") or ""
    if not tesseract:
        return ""
    import subprocess
    try:
        r = subprocess.run([tesseract, path, "-"], capture_output=True, timeout=120)
        return r.stdout.decode("utf-8", "ignore")
    except Exception:
        return ""


# ============================================================
# 一、抽取（按扩展名分派）
# ============================================================
def read_docx(path):
    """从 .docx 抽取纯文本（跳过页眉/页脚/水印，仅取正文 document.xml）。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{{{W_NS}}}p"):
        runs = [t.text or "" for t in p.iter(f"{{{W_NS}}}t")]
        paras.append("".join(runs))
    return "\n".join(paras)


def extract(path):
    """统一抽取入口。返回 (text, meta)。
    meta: {name(文件名), type(扩展名小写), size(字节), date(YYYY-MM-DD 修改日)}
    """
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    meta = {
        "name": name,
        "type": ext,
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
        "date": _fmt_date(os.path.getmtime(path)) if os.path.exists(path) else "",
    }
    if ext == ".txt":
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    elif ext == ".md":
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    elif ext == ".docx":
        text = read_docx(path)
    elif ext == ".pptx":
        # 复用已有解析器，把每页正文+备注拼成连续文本
        import pptx_parse
        slides = pptx_parse.parse(path)
        parts = []
        for sl in slides:
            head = f"【PPT第{sl['idx']}页 {sl.get('title','')}】"
            body = sl.get("text", "")
            note = f"\n（备注）{sl['notes']}" if sl.get("notes") else ""
            parts.append(head + body + note)
        text = "\n\n".join(parts)
        meta["type"] = ".pptx"
    elif ext == ".pdf":
        text = _extract_pdf(path)
    elif ext in (".xlsx", ".xlsm"):
        text = _extract_xlsx(path)
        meta["type"] = ".xlsx"
    elif ext == ".csv":
        text = _extract_csv(path)
        meta["type"] = ".csv"
    elif ext in (".html", ".htm", ".mhtml"):
        text = _extract_html(path)
    elif ext == ".json":
        text = _extract_json(path)
    elif ext in (".epub", ".epub3"):
        text = _extract_epub(path)
    elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"):
        text = _extract_image(path)
    # elif ext == ".txt" / ".md" 走上面的原生分支（.md 也直接读）
    else:
        raise ValueError(
            f"不支持的文档类型: {ext}（支持 .txt/.md/.docx/.pptx/.pdf/.xlsx/.csv/.html/.json/.epub/.jpg/.png）")
    return text, meta


# ============================================================
# 一·三、目录批量（WeKnora 的「数据源」思路：整个文件夹当成一个导入源）
# ============================================================
SUPPORTED_EXT = {".txt", ".md", ".docx", ".pptx", ".pdf", ".xlsx", ".xlsm",
                 ".csv", ".html", ".htm", ".json", ".epub", ".jpg", ".jpeg",
                 ".png", ".bmp", ".webp", ".gif"}


def iter_files(root, exts=None, recursive=True):
    """遍历目录，产出 (绝对路径, 扩展名)，跳过 node_modules/__pycache__/.git 等。"""
    exts = exts or SUPPORTED_EXT
    root = os.path.abspath(root)
    if os.path.isfile(root):
        ext = os.path.splitext(root)[1].lower()
        return [(root, ext)] if ext in exts else []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for fn in filenames:
            if fn.startswith("~$") or fn.startswith("."):
                continue                              # ~$ 是 Office 临时锁文件，. 开头是隐藏文件
            ext = os.path.splitext(fn)[1].lower()
            if ext in exts:
                out.append((os.path.join(dirpath, fn), ext))
    return sorted(out)


def ingest_dir(paths, name="myme_kb", category="", model=None, on_progress=None):
    """批量导入（文件或目录）。返回 {ok: [ {path, added, name} ], fail: [ {path, err} ], skipped}。

    ⚠ 单文件失败不能中断整批（一份坏 PDF 不该让 30 份好文档一起入库失败），
      所以这里逐个 try，失败项收进 fail 里并带上原因。
    """
    import knowledge_base
    files = []
    for p in paths:
        for fp, _ext in iter_files(p):
            files.append(fp)
    ok, fail = [], []
    for fp in files:
        try:
            r = knowledge_base.ingest_document(fp, name=name, category=category, model=model)
            if on_progress:
                on_progress(fp, r)
            if r.get("skipped") in ("empty", "unchanged"):
                continue
            ok.append({"path": fp, "added": r.get("added", 0), "name": r.get("name")})
        except Exception as e:
            fail.append({"path": fp, "err": "%s: %s" % (type(e).__name__, e)})
    return {"ok": ok, "fail": fail, "scanned": len(files)}


# ============================================================
# 二、清洗（去噪 + 脱敏）
# ============================================================
_SENSITIVE = [
    # 顺序很重要：先身份证(18位)再手机号，否则手机号规则会吃掉身份证中的数字串
    ("idcard", re.compile(r"\d{17}[\dXx]")),
    ("phone", re.compile(r"1[3-9]\d{9}")),
    ("email", re.compile(r"[\w.+-]+@[\w.-]+\.\w+")),
]


def remove_sensitive(text):
    """用正则识别并替换敏感信息（默认脱敏，保护隐私）。"""
    for label, pat in _SENSITIVE:
        text = pat.sub(f"[{label}已脱敏]", text)
    return text


# 只剥离「真正的 HTML 标签」：按 HTML 元素名白名单匹配。
# 早期用 <[^>]+> 一刀切，会把技术正文里的尖括号一起吃掉——
# 例如 <input type=file> 被删空、/proc/<pid>/fd 变成 /proc//fd、<占位字段> 直接消失。
# 白名单之外的尖括号内容（多为代码符号 / 占位变量）属于有效信息，必须保留。
_HTML_TAG = re.compile(
    r"</?(?:html|head|body|title|meta|link|script|style|div|span|p|br|hr|"
    r"img|a|table|thead|tbody|tr|td|th|ul|ol|li|dl|dt|dd|h[1-6]|strong|b|i|u|em|"
    r"font|center|small|big|blockquote|pre|code|figure|figcaption|section|article|"
    r"header|footer|nav|main|aside|form|input|button|label|select|option|textarea|"
    r"iframe|sup|sub|strike|del|ins|caption|colgroup|col)\b[^>]{0,200}>",
    re.I)


def clean_text(text, mask_sensitive=True):
    """入库前清洗：去 HTML 标签、去常见噪音行、折叠空白。"""
    if mask_sensitive:
        text = remove_sensitive(text)
    # 去 HTML 标签（仅白名单元素名，保留 <pid> 这类技术占位/代码符号）
    text = _HTML_TAG.sub("", text)
    # 去典型页眉/页脚/水印噪音行（大小写不敏感）
    noise = re.compile(r"^\s*(页眉|页脚|水印|保密|机密|confidential|draft|第\s*\d+\s*页|copyright).*$", re.I)
    lines = [ln for ln in text.splitlines() if not noise.match(ln)]
    text = "\n".join(lines)
    # 折叠多余空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fmt_date(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ============================================================
# 三、文件名归一化（【分类】标题｜来源｜日期）
# ============================================================
def normalize_title(filename, category=""):
    """把文件名变成知识库友好标题：去掉扩展名，下划线转空格。"""
    base = os.path.splitext(filename)[0]
    base = base.replace("_", " ").replace("-", " ").strip()
    if category:
        return f"【{category}】{base}"
    return base


# ============================================================
# 四、指纹（增量去重）
# ============================================================
def file_hash(path, algo="sha256"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python doc_ingest.py <file.txt/.md/.docx/.pptx>")
        sys.exit(0)
    p = sys.argv[1]
    t, m = extract(p)
    c = clean_text(t)
    print(f"源: {m['name']}  类型:{m['type']}  大小:{m['size']}  日期:{m['date']}")
    print(f"清洗后字符数: {len(c)}")
    print("---- 前 300 字 ----")
    print(c[:300])
    print("---- hash ----")
    print(file_hash(p))
