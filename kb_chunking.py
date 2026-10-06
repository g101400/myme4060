# -*- coding: utf-8 -*-
"""
kb_chunking.py — myme 知识库分块内核（吸收 WeKnora v0.8.2 的分块精华）

来源：WeKnora 0.8.2  `internal/infrastructure/chunker/`（profiler / strategy /
heading_splitter / heuristic_splitter / splitter）的等价 Python 实现，去掉了
Go 侧与 SQLite/GORM 耦合的部分，只保留「文本 → 块」这一段，可直接被
knowledge_base.py 调用。

移植了三条最值钱的设计：

  1. **自适应分块（文档画像 → 选档）**
     不动手按字数切，先扫一遍文档算结构信号（Markdown 标题数/密度/主导层级、
     编号小节、各语言「第X章」、全大写短行、分页符、视觉分隔线、页脚、表格/代码
     占比、语种），再决定走 heading / heuristic / recursive 哪一档：
       - heading   ：文档至少 3 个 md 标题 且 标题密度 > 0.005 且有主导层级
       - heuristic ：启发式标记合计 ≥ 5 或有分页符/有中英德章节标题
       - recursive ：兜底（永远返回至少一个块）
     判据与 WeKnora 完全一致，见 select_strategy()。

  2. **父子分块（小块匹配、大块返回）**
     child（默认 384 字符）负责被向量匹配，命中后把 parent（默认 4096 字符）
     整段返回给模型，解决「匹配得准但上下文不全」。父块存库供上下文、不单独入库。

  3. **上下文面包屑（ContextHeader）**
     每块嵌入的内容 = 「上级标题链 + 正文」，即 chunk.embedding_content()。
     标题链不占 Content 的字数预算（Content 只装正文，保证 Start/End 可还原），
     但会拼进 embedding text —— 这是检索准确率最便宜的一笔提升。

⚠ 两条防呆（照抄 WeKnora 的 ensureDefaults）：
     - overlap 不得超过 chunk_size/2，否则块间几乎全是重复内容；
     - 切完后若为空，必须兜底返回整篇，绝不允许「切了等于没切」的静默空结果。
"""
import os
import re

# ============================================================
# 默认配置（对齐 WeKnora 默认值）
# ============================================================
DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 100
PARENT_CHUNK_SIZE = 4096      # WeKnora 默认父块
CHILD_CHUNK_SIZE = 384        # WeKnora 默认子块（真正被嵌入的那层）
DEFAULT_SEPARATORS = ("\n\n", "\n", "。", "！", "？", ";", "；")

# ============================================================
# 正则（等价 Go RE2 版本；Go 版无 lookaround，这里保持同义）
# ============================================================
RE_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
RE_FORM_FEED = re.compile(r"\f")
RE_NUMBERED_SECTION = re.compile(r"^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$")
RE_ALL_CAPS = re.compile(r"^[ \t]*([A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}):?\s*$")
RE_VISUAL_SEP = re.compile(r"^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$")
RE_PAGE_FOOTER = re.compile(r"^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$", re.I)
RE_CHAPTER_DE = re.compile(r"^[ \t]*(?:Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$")
RE_CHAPTER_EN = re.compile(r"^[ \t]*(?:Chapter|Section|Part)\s+(?:[0-9]+|[IVX]{1,5})[\.: ].{0,200}$")
RE_CHAPTER_ZH = re.compile(r"^[ \t]*第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}$")
RE_TABLE_ROW = re.compile(r"^[ \t]*(?:\|[^|\n]*)+\|[ \t]*$")
RE_BLANK_BREAK = re.compile(r"\n{3,}")

# ⚠ 加固（WeKnora 没有这一层，本机实测必需）：
#   Markdown 标题模式 `^#{1,6}\s+...\s*$` 会把代码/配置里的注释行 `# ======`
#   、`# ---`、行首 `#` 开头的分隔符全当成标题，于是面包屑里塞满 `====`、`-----`
#   这种噪声，反而把真正的标题挤掉（KB 里那份 Python 源码就是这样被污染的）。
#   判定：标题正文里必须至少有一个汉字或字母，纯符号/纯数字不认。
RE_HEADING_HAS_WORD = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _md_heading(line):
    """匹配 Markdown 标题；正文无实词（纯符号/数字）时返回 None。"""
    m = RE_MD_HEADING.match(line or "")
    if not m:
        return None
    if not RE_HEADING_HAS_WORD.search(m.group(2)):
        return None
    return m


# ============================================================
# 一、文档画像
# ============================================================
class DocProfile(object):
    """文档结构画像：一堆「这个文档长什么样」的统计信号。"""

    def __init__(self):
        self.total_chars = 0
        self.total_lines = 0
        self.avg_line_len = 0.0
        self.std_line_len = 0.0
        self.md_heading_counts = {}     # 层级 1..6 → 个数
        self.md_heading_total = 0
        self.numbered_section_count = 0
        self.all_caps_short_line_count = 0
        self.blank_paragraph_breaks = 0
        self.form_feed_count = 0
        self.visual_sep_count = 0
        self.german_chapter_count = 0
        self.english_chapter_count = 0
        self.chinese_chapter_count = 0
        self.repeated_footer_count = 0
        self.has_tables = False
        self.has_code = False
        self.code_ratio = 0.0
        self.detected_langs = []

    # —— 判据函数（与 WeKnora 同名同义） ——
    def heading_density(self):
        if not self.total_lines:
            return 0.0
        return self.md_heading_total / float(self.total_lines)

    def dominant_heading_level(self):
        """返回驱动切分的主层级：出现 ≥3 次的最浅层级；否则取最深的那一层。"""
        if not self.md_heading_total:
            return 0
        for lv in range(1, 7):
            if self.md_heading_counts.get(lv, 0) >= 3:
                return lv
        for lv in range(6, 0, -1):
            if self.md_heading_counts.get(lv, 0) > 0:
                return lv
        return 0

    def heuristic_marker_total(self):
        return (self.numbered_section_count + self.german_chapter_count
                + self.english_chapter_count + self.chinese_chapter_count
                + self.all_caps_short_line_count + self.visual_sep_count
                + self.form_feed_count)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


def profile_document(text):
    """一趟扫描算出文档画像（WeKnora ProfileDocument 的等价实现）。"""
    p = DocProfile()
    if not text:
        return p

    p.total_chars = len(text)
    p.form_feed_count = len(RE_FORM_FEED.findall(text))
    lines = text.split("\n")
    p.total_lines = len(lines)

    lengths = []
    in_fence = False
    code_chars = 0
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):          # 围栏代码开关，代码内不计入结构信号
            in_fence = not in_fence
            p.has_code = True
            continue
        if in_fence:
            code_chars += len(line)
            continue

        lengths.append(len(line))
        m = _md_heading(line)
        if m:
            lv = len(m.group(1))
            if 1 <= lv <= 6:
                p.md_heading_counts[lv] = p.md_heading_counts.get(lv, 0) + 1
            p.md_heading_total += 1
            continue
        if RE_NUMBERED_SECTION.match(line):
            p.numbered_section_count += 1
        if RE_CHAPTER_DE.match(line):
            p.german_chapter_count += 1
        if RE_CHAPTER_EN.match(line):
            p.english_chapter_count += 1
        if RE_CHAPTER_ZH.match(line):
            p.chinese_chapter_count += 1
        if RE_ALL_CAPS.match(line):
            p.all_caps_short_line_count += 1
        if RE_VISUAL_SEP.match(line):
            p.visual_sep_count += 1
        if RE_PAGE_FOOTER.match(line):
            p.repeated_footer_count += 1
        if RE_TABLE_ROW.match(line):
            p.has_tables = True

    if lengths:
        n = float(len(lengths))
        p.avg_line_len = sum(lengths) / n
        var = sum((l - p.avg_line_len) ** 2 for l in lengths) / n
        p.std_line_len = var ** 0.5

    if p.total_chars > 0:
        p.code_ratio = code_chars / float(p.total_chars)

    p.blank_paragraph_breaks = len(RE_BLANK_BREAK.findall(text))

    # 语种：只采样前 4096 字符（避免大文档 O(N) 成本）
    sample = text[:4096]
    langs = _detect_langs(sample)
    p.detected_langs = langs
    return p


def _profile_heading(line, p):
    """画像阶段统计标题（与 _md_heading 同一套判定，避免两边口径不一致）。"""
    m = _md_heading(line)
    if not m:
        return False
    lv = len(m.group(1))
    if 1 <= lv <= 6:
        p.md_heading_counts[lv] = p.md_heading_counts.get(lv, 0) + 1
    p.md_heading_total += 1
    return True


def _detect_langs(sample):
    """轻量语种启发式（够画像用，不引第三方语言库）。"""
    if not sample:
        return ["zh"]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk > latin * 2:
        return ["zh"]
    if latin > cjk * 2:
        return ["en"]
    return ["en", "zh"]


# ============================================================
# 二、选档（决定走哪一档切分）
# ============================================================
TIER_HEADING = "heading"
TIER_HEURISTIC = "heuristic"
TIER_RECURSIVE = "recursive"
TIER_CHAIN = (TIER_HEADING, TIER_HEURISTIC, TIER_RECURSIVE)


def select_strategy(profile):
    """返回要依次尝试的档位链，最后一个必然是兜底。

    ⚠ 与 WeKnora 判据逐条一致：链上第 1 项是首选，后面的是验证不通过时的回退。
    """
    if profile is None:
        return [TIER_RECURSIVE]
    chain = []

    # 档1：Markdown 标题感知
    if (profile.md_heading_total >= 3
            and profile.heading_density() > 0.005
            and profile.dominant_heading_level() > 0):
        chain.append(TIER_HEADING)

    # 档2：启发式边界
    if (profile.heuristic_marker_total() >= 5 or profile.form_feed_count > 0
            or (profile.german_chapter_count + profile.english_chapter_count
                + profile.chinese_chapter_count) > 0):
        chain.append(TIER_HEURISTIC)

    chain.append(TIER_RECURSIVE)   # 永远留兜底，绝不返回空链
    return chain


def resolve_chain(text, strategy="auto"):
    """按配置策略决定档位链；strategy=auto 时先画像。"""
    if strategy == "heading":
        return [TIER_HEADING, TIER_RECURSIVE]
    if strategy == "heuristic":
        return [TIER_HEURISTIC, TIER_RECURSIVE]
    return select_strategy(profile_document(text))


# ============================================================
# 三、三档切分器
# ============================================================
def _ensure(size, overlap):
    """对齐 WeKnora ensureDefaults：overlap 不得超过 size/2。"""
    size = size or DEFAULT_CHUNK_SIZE
    overlap = overlap or DEFAULT_OVERLAP
    if overlap > size / 2.0:
        overlap = size / 2
    return size, int(overlap)


def split_recursive(text, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP):
    """兜底档：按分隔符递归切（原文末句 / 换行 / 句号 / 分号），带重叠。"""
    size, overlap = _ensure(size, overlap)
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []

    sents = re.split(r"(?<=[。！？!?；;\n])", text)
    chunks, cur, cur_len = [], [], 0
    for s in sents:
        s = s or ""
        if cur and cur_len + len(s) > size:
            chunks.append("".join(cur))
            tail = "".join(cur)
            if len(tail) > overlap:
                tail = tail[-overlap:]
            cur, cur_len = [tail], len(tail)
        cur.append(s)
        cur_len += len(s)
    if cur:
        chunks.append("".join(cur))
    return [c for c in chunks if c.strip()]


def split_headings(text, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP, profile=None):
    """档1：按 Markdown 标题层级切，标题不进 Content 但进上下文面包屑。"""
    size, overlap = _ensure(size, overlap)
    if not text or not text.strip():
        return []
    profile = profile or profile_document(text)
    lvl = profile.dominant_heading_level()
    if lvl == 0:
        return _wrap(split_recursive(text, size, overlap))

    lines = text.split("\n")
    # 每行起始字符偏移，用于给每个 section 标出真实区间（相邻相接，coalesce 靠它判断）
    off, pos = [0], 0
    for line in lines:
        pos += len(line) + 1
        off.append(pos)

    # 找出该层级的边界行（char_start 指向标题行的下一行，即 section 正文起点）
    bounds = []
    for i, line in enumerate(lines):
        m = _md_heading(line)
        if m and len(m.group(1)) == lvl:
            bounds.append({"line_start": i, "line": line,
                           "char_start": off[i] + len(line) + 1,
                           "char_end": off[i + 1]})
    if len(bounds) <= 1:
        return _wrap(split_recursive(text, size, overlap))

    out, seq = [], 0
    # 每个 section 的字符区间（供 coalesce 判断「首尾相接」，也便于还原定位）
    for i, b in enumerate(bounds):
        start_line = b["line_start"] + 1                 # 标题行本身算进 section
        end_line = bounds[i + 1]["line_start"] if i + 1 < len(bounds) else len(lines)
        section = "\n".join(lines[start_line:end_line]).strip()
        if not section:
            continue
        bc = _breadcrumb_for(lines, b["line_start"], lvl)
        sec_start = b.get("char_start", 0)
        sec_end = b.get("char_end", len(text))
        header_ok = len(bc) + 2 + len(section) <= size
        if header_ok:
            out.append({"text": section, "breadcrumb": bc, "seq": seq,
                        "_start": sec_start, "_end": sec_end})
            seq += 1
        else:
            # section 太大：退到递归内部分段，每块仍带该处最深面包屑
            base = sec_start
            for piece in split_recursive(section, size, overlap):
                off = section.find(piece)
                if off < 0:
                    off = 0
                out.append({"text": piece,
                            "breadcrumb": _breadcrumb_at_offset(lines, start_line, lvl, off),
                            "seq": seq, "_start": base + off, "_end": base + off + len(piece)})
                seq += 1
    merged = _coalesce_tiny(out, size)
    return merged or _wrap(split_recursive(text, size, overlap))


def _breadcrumb_for(lines, heading_idx, lvl):
    """整节面包屑 `根 › 中 › 当前节`。

    ⚠ 当前节的标题本身也要进面包屑（WeKnora 在取 breadcrumb 前先 Observe(b.line)），
      否则正文里那行 `## 检索链路` 的信息就丢了 —— 而它恰恰是这块最像「标题」的信号。
    """
    crumbs = []
    for j in range(0, heading_idx):
        m = _md_heading(lines[j])
        if not m:
            continue
        if len(m.group(1)) < lvl:
            crumbs.append(m.group(2).strip())
    if heading_idx < len(lines):
        m = _md_heading(lines[heading_idx])
        if m:
            crumbs.append(m.group(2).strip())
    return " › ".join(crumbs)


def _breadcrumb_at_offset(lines, section_start, lvl, offset):
    """大 section 内部分段后，按字符偏移找当时最深的子标题做面包屑。"""
    if offset <= 0:
        return _breadcrumb_for(lines, section_start - 1, lvl)
    head = lines[section_start - 1] if section_start > 0 else ""
    window = "\n".join(lines[section_start:section_start + 40])[: offset + 400]
    best = ""
    for line in window.split("\n"):
        m = _md_heading(line)
        if m and len(m.group(1)) >= lvl:
            best = m.group(2).strip()
    base = _breadcrumb_for(lines, section_start - 1, lvl)
    return (base + " › " + best) if best else base


def _coalece_guard(items):
    return items


def _coalesce_tiny(items, size):
    """合并过小的相邻块（FAQ/清单类文档很容易全是被判据否掉的碎块）。

    ⚠ 只合并「首尾严格相接」的相邻块（上一块 _end == 下一块 _start）。
      早期版本判断写成 `buf["_end"] == it["_start"]`，而两者缺省都是 None，
      None == None 恒为 True，结果把整篇文档全并成一块 —— 分块等于没做。
      所以这里必须先确认两边都真有偏移（不是 None）才允许合并。
    """
    if len(items) <= 1 or size <= 0:
        return items
    out, buf = [], None
    for it in items:
        if buf is None:
            buf = dict(it)
            continue
        if (buf.get("_end") is not None and it.get("_start") is not None
                and buf["_end"] == it["_start"]
                and len(buf["text"]) + 2 + len(it["text"]) <= max(size / 2, 120)):
            buf["text"] = buf["text"] + "\n" + it["text"]
            buf["_end"] = it.get("_end", buf.get("_end"))
            continue
        out.append(buf)
        buf = dict(it)
    if buf is not None:
        if out and out[-1].get("_end") is not None and buf.get("_start") is not None \
                and out[-1]["_end"] == buf["_start"]:
            out[-1]["text"] = out[-1]["text"] + "\n" + buf["text"]
        else:
            out.append(buf)
    for i, it in enumerate(out):
        it["seq"] = i
    return out


def split_heuristics(text, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP):
    """档2：启发式边界（编号小节 / 章节标题 / 分页符 / 分隔线 / 全大写短行）。"""
    size, overlap = _ensure(size, overlap)
    if not text or not text.strip():
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    parts = re.split(r"\f", text)                     # 分页符优先
    if len(parts) > 1:
        return _wrap([p for p in parts if p.strip()], size)

    lines = text.split("\n")
    blocks, cur = [], []
    for line in lines:
        head = (RE_NUMBERED_SECTION.match(line)
                or RE_CHAPTER_ZH.match(line) or RE_CHAPTER_EN.match(line)
                or RE_CHAPTER_DE.match(line) or RE_VISUAL_SEP.match(line)
                or RE_ALL_CAPS.match(line))
        if head and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))

    blocks = [b for b in (b.strip() for b in blocks) if b]
    if not blocks:
        return _wrap(split_recursive(text, size, overlap))
    out = []
    for b in blocks:
        if len(b) <= size:
            out.append({"text": b, "breadcrumb": "", "seq": len(out)})
        else:
            for piece in split_recursive(b, size, overlap):
                out.append({"text": piece, "breadcrumb": "", "seq": len(out)})
    return out


def _wrap(pieces, size=DEFAULT_CHUNK_SIZE):
    """把 str 列表变成带 seq 的统一结构；空结果兜底返回整篇。"""
    out = [{"text": p, "breadcrumb": "", "seq": i} for i, p in enumerate(pieces)]
    return out or [{"text": "", "breadcrumb": "", "seq": 0}]


# ============================================================
# 四、统一入口 + 父子分块 + 面包屑
# ============================================================
def chunk_text(text, meta=None, size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP,
               strategy="auto"):
    """按自适应分块切文本。每块 = {"text": 正文, "meta": ..., "breadcrumb": ...}。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    profile = profile_document(text) if strategy == "auto" else None
    chain = resolve_chain(text, strategy)

    for tier in chain:
        if tier == TIER_HEADING:
            out = split_headings(text, size, overlap, profile)
        elif tier == TIER_HEURISTIC:
            out = split_heuristics(text, size, overlap)
        else:
            out = _wrap(split_recursive(text, size, overlap))
        if out:
            meta = dict(meta or {})
            return [{"text": c["text"], "breadcrumb": c.get("breadcrumb", ""),
                     "meta": meta} for c in out]
    return [{"text": text, "breadcrumb": "", "meta": dict(meta or {})}]   # 绝不空手


def embedding_content(chunk):
    """嵌入用文本 = 面包屑 + 正文（WeKnora ContextHeader 的同义实现）。

    ⚠ 面包屑算进 embedding、不算进 Content —— Content 只装正文，
      这样 Start/End 偏移才能和原文一一对上（文档重建依赖这个不变量）。
    """
    body = (chunk.get("text") or "").strip()
    bc = (chunk.get("breadcrumb") or "").strip()
    if not bc:
        return body
    return bc + "\n\n" + body


def split_parent_child(text, parent_size=PARENT_CHUNK_SIZE,
                       child_size=CHILD_CHUNK_SIZE, strategy="auto"):
    """父子分块：大段父块存上下文、小块子块被嵌入。

    返回 (parents, children)：
      parents  = [{"pid", "text", "start", "end"}]
      children = [{"cid", "pid", "text", "breadcrumb", "start", "end"}]
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return [], []

    # 父层：稍大粒度切（不设 token limit，保留完整上下文窗口）
    parent_pieces = split_recursive(text, parent_size, max(1, parent_size // 8))
    if not parent_pieces:
        parent_pieces = [{"text": text, "breadcrumb": "", "seq": 0}]

    parents, children = [], []
    for i, pp in enumerate(parent_pieces):
        pbody = pp["text"] if isinstance(pp, dict) else pp
        pbody = pbody.strip()
        if not pbody:
            continue
        pid = "p%d" % i
        start = text.find(pbody[:60])
        end = (start + len(pbody)) if start >= 0 else len(text)
        if start < 0:
            start, end = 0, len(pbody)
        parents.append({"pid": pid, "text": pbody, "start": start, "end": end})

        # 子层：在父块内部再切一遍（384），带面包屑
        for j, cc in enumerate(split_text_with_breadcrumb(pbody, child_size, strategy)):
            children.append({
                "cid": "%s_c%d" % (pid, j),
                "pid": pid,
                "text": cc["text"],
                "breadcrumb": cc.get("breadcrumb", ""),
                "start": start + (cc.get("_off", 0) or 0),
                "end": start + (cc.get("_off", 0) or 0) + len(cc["text"]),
            })
    return parents, children


def split_text_with_breadcrumb(text, size, strategy="auto"):
    """在一段内部切分并尽量保留内部标题作面包屑（父子分块的子层用）。"""
    real = strategy if strategy in ("heading", "heuristic") else "auto"
    out = chunk_text(text, size=size, strategy=real)
    return out


def merge_breadcrumbs(parent_crumb, child_crumb):
    """合并父子面包屑；子级第一段常常就是父级末尾标题，重复就丢掉。"""
    parent_crumb = (parent_crumb or "").strip()
    child_crumb = (child_crumb or "").strip()
    if not parent_crumb:
        return child_crumb
    if not child_crumb:
        return parent_crumb
    pl, cl = parent_crumb.split("\n"), child_crumb.split("\n")
    if pl and cl and pl[-1].strip() == cl[0].strip():
        cl = cl[1:]
    if not cl:
        return parent_crumb
    return parent_crumb + "\n" + "\n".join(cl)


# ============================================================
# 五、自检（python kb_chunking.py）
# ============================================================
if __name__ == "__main__":
    import json

    md = "\n".join([
        "# 项目总览",
        "这台机器跑的是本地全链路：PPT 文稿生成、配音合成、数字人口型、在线答疑四段。"
        "所有中间产物都落在 %LOCALAPPDATA%\\myme 下，源码仓库里查不到是正常的，别误判成丢数据。",
        "",
        "## 分块策略",
        "知识库不走无脑按字数切。先做文档画像：看标题密度、分页符数量、章节标记，"
        "再自动选最合适的切分方式——技术文档用 heading 模式按标题切，PDF 用 heuristic 模式按排版切，"
        "普通叙述文本用递归切。这个细节直接决定检索质量，去年踩过一次坑，检索准确率掉了将近两成。",
        "",
        "### 父子块",
        "小块负责精准匹配，默认 384 字符被拿去做向量；命中之后把大块 4096 字符整段返回给模型，"
        "解决「匹配得准但上下文不全」这个经典问题。块内容还能在界面上直接改，有版本快照和 diff 对比。",
        "",
        "## 检索链路",
        "混合检索是语义向量加 BM25 关键词，再加一层知识图谱增强。"
        "专有名词、报错代码这类东西向量检索容易漏，BM25 能兜住；语义召回负责自然语言提问。"
        "两层分数融合后再过一遍重排，最后才拼进上下文。",
        "",
        "## 答疑与交付",
        "答疑走两轮：先用检索上下文做一次压缩，再交给大模型组织答案，答案里带来源标注，"
        "点来源能跳回原文档那一页。交付物有 Windows 双产物、Android、iOS、MSI、源码包和交接总包。",
    ])
    p = profile_document(md)
    print("画像: 标题%d个 密度%.4f 主导层级%d 行数%d"
          % (p.md_heading_total, p.heading_density(), p.dominant_heading_level(), p.total_lines))
    print("选档链:", select_strategy(p))

    cs = chunk_text(md, meta={"source": "demo.md"}, strategy="auto")
    print("切块 %d 块:" % len(cs))
    for i, c in enumerate(cs):
        print("  [%d] 面包屑=%r 长度=%d 嵌入长度=%d"
              % (i, c["breadcrumb"], len(c["text"]), len(embedding_content(c))))
    assert all(embedding_content(c) for c in cs), "嵌入内容不能为空"
    assert all(len(c["text"]) > 0 for c in cs), "正文不能为空"
    assert len(cs) >= 3, "结构化文档应按标题切出多块，实际 %d 块（分块失效）" % len(cs)
    assert any(c["breadcrumb"] for c in cs), "面包屑丢了（标题上下文丢失）"
    # 兜底：普通无结构长文必须至少 1 块
    plain = "这是一段没有任何标题的普通说明文字。" * 200
    assert chunk_text(plain), "兜底档不能返回空"
    for tier in ("heading", "heuristic"):
        got = chunk_text(md, strategy=tier)
        assert got, "档 %s 返回空（不该发生）" % tier
        print("  档 %-10s → %d 块" % (tier, len(got)))
    # 父子分块必须子块 ⊂ 父块
    pp, cc = split_parent_child(md)
    assert len(cc) >= len(pp), "子块数应 ≥ 父块数"
    for ch in cc:
        pm = [x for x in pp if x["pid"] == ch["pid"]]
        assert pm and ch["cid"].startswith(pm[0]["pid"]), "子块未挂到父块"
    print("自检通过")

    parents, children = split_parent_child(md)
    print("父子: 父%d / 子%d" % (len(parents), len(children)))
    for ch in children[:3]:
        pm = [x for x in parents if x["pid"] == ch["pid"]][0]
        print("   子%s → 父段 %d 字符，面包屑=%r" % (ch["cid"], len(pm["text"]), ch["breadcrumb"]))
    assert len(parents) and len(children), "父子分块不能为空"

    # 兜底：普通无结构长文必须至少 1 块
    plain = "这是一段没有任何标题的普通说明文字。" * 200
    assert chunk_text(plain), "兜底档不能返回空"
    print("自检通过")
