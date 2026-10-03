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
import hashlib
import zipfile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


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
    else:
        raise ValueError(f"不支持的文档类型: {ext}（支持 .txt/.md/.docx/.pptx）")
    return text, meta


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
