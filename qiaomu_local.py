# -*- coding: utf-8 -*-
"""qiaomu_local —— 多源搬运工（国内离线版）

移植自 github.com/joeseesun/qiaomu-anything-to-notebooklm，吸收其三条设计精华：
  1) 统一入口 + 输入类型自动识别（域名 / 后缀 / 关键词三级分派）
  2) 不同来源走不同的提取策略（微信文章、播客转写、推文、网页付费墙绕过…）
  3) 产出意图映射表（播客 / PPT / 思维导图 / Quiz / 报告 …）

全部砍掉国内不可达的依赖，换成本机自己的链路（v2.9 又做了一次「该换的换、该删的删」）：

  删除（原项目依赖、国内不可达，给明确替代路径而不是静默失败）
    YouTube / X(Twitter) / Medium   → 删源；视频改投 B站，短内容改投微博/公众号，
                                      客户端内的内容改「复制分享文案 → 粘贴导入」
                                      （抖音 / 小红书 / 快手同属登录墙，统一归 paste 类）
  替换成国内可达平台
    NotebookLM CLI     → 本地 Ollama 向量知识库 knowledge_base
    微信 read MCP      → 直连 mp.weixin.qq.com（正文 #js_content + Referer 防盗链）
    Get 笔记 API       → 本地 ASR（asr_listen）+ 平台字幕接口兜底
    Google / r.jina.ai → 必应 → 百度（_web_search），国内直连 + UA/Referer 伪装
    r.jina.ai 代理级联 → 国内直连；AMP 兜底保留但降级为最后手段
    飞书 lark-cli      → 本地 Markdown / PPTX 落盘
  新增国内源
    微博（m.weibo.cn 详情接口）、今日头条、百家号、微信读书，
    以及水利部 / 生态环境部 / 中国古迹遗址保护协会 / 海康 · 大华 · 华为等厂商文档站；
    政务站（*.gov.cn）自动套用 #content / TRS_Editor 口径。

用法：
    import qiaomu_local
    r = qiaomu_local.run("https://mp.weixin.qq.com/s/xxxx")   # 自动识别+抓取+入库
    r = qiaomu_local.run("D:/a.pdf", kb="myme_kb")
    r = qiaomu_local.run("清淤工期")                            # 关键词 → 查本地库
CLI:
    python qiaomu_local.py "https://mp.weixin.qq.com/s/xxx" --kb myme_kb
"""

import os
import re
import sys
import json
import time
import gzip
import zlib
import html as _html
import traceback
import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 付费墙/登录墙常用：搜索引擎爬虫常拿到全文（站点为了 SEO 给爬虫放行）
CRAWLER_UA = ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BINGBOT_UA = ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TIMEOUT = 30

# ==================================================================
# 站点适配表：域名 → (来源名, 去噪选择器, 正文选择器)
# 这是 qiaomu「针对大量网站做内容提取适配」在国内站点的落地版
# ==================================================================
SITE_RULES = {
    # —— 内容社区（国内可达）
    "mp.weixin.qq.com":  ("微信公众号", None, None),          # 走专项解析
    "xiaoyuzhoufm.com":  ("小宇宙播客", None, None),          # 走音频转写
    "ximalaya.com":      ("喜马拉雅", None, None),            # 走音频转写
    "bilibili.com":      ("B站视频", None, None),             # 走字幕/ASR
    "weibo.com":         ("微博", [".WB_cardwrap", ".WB_app_cards"], [".wb-text", ".txt", ".woo-box-flex"]),
    "toutiao.com":       ("今日头条", ["aside", "footer", ".document-side"],
                          ["article", ".article-content", ".tt-article"]),
    "baijiahao.baidu.com": ("百家号", [".doc-footer", ".recommend-list"],
                            ["#article_content", ".article-content"]),
    "weread.qq.com":     ("微信读书", None, [".chapter-content", "#chapterContent"]),
    "zhihu.com":         ("知乎", [".QuestionHeader", ".AnswerItem", ".TopstoryItem", ".RichText"],
                          [".RichText", ".Highlight", ".AnswerBody"]),
    "juejin.cn":         ("掘金", ["#js_profile_card", ".article-pretty", ".journal-entry"],
                          [".article-content", ".markdown-body"]),
    "csdn.net":          ("CSDN", [".blog-content", ".article-content", ".csdn-rich-text"],
                          [".blog-content", ".article-content", ".markdown-body"]),
    "jianshu.com":       ("简书", [".open-app", "#app"], [".article", ".content"]),
    "sspai.com":         ("少数派", [".app-content"], [".markdown-body"]),
    "36kr.com":          ("36氪", [".common-width", ".article"], [".article-content"]),
    "infoq.cn":          ("InfoQ", [".post-body"], [".post-body"]),
    # —— 水利 / 古建 / 感知（主战场：能定位到的官方与厂商源）
    "water.gov.cn":      ("水利部", None, None),
    "mwr.gov.cn":        ("水利部", None, None),
    "mee.gov.cn":        ("生态环境部", None, None),
    "ihchina.cn":        ("中国古迹遗址保护协会", None, None),
    "hikvision.com":     ("海康威视", None, None),
    "dahuatech.com":     ("大华技术", None, None),
    "huaweicloud.com":   ("华为云", None, None),
    ".gov.cn":           ("政务网站", [".nav", ".footer", ".sidebar"], ["#content", ".view", "TRS_Editor"]),
}
# 这些站点国内可达但必须登录/App 内打开，抓不到正文 —— 统一引导「复制分享文案再粘贴」
PASTE_ONLY_HOSTS = {"douyin.com", "xiaohongshu.com", "kuaishou.com", "instagram.com"}
# 已经从本版移除的源（原 qiaomu 依赖的 Google / YouTube / X / r.jina.ai 等，国内不可达）
REMOVED_HOSTS = {"youtube.com", "youtu.be", "x.com", "twitter.com", "medium.com"}
DEFAULT_RULE = ("网页", [".site-header", ".header", ".nav", ".footer", ".sidebar",
                     "#js_top_ad", ".ad-wrap", ".recommend", ".comment"],
                ["#content", ".content", ".article", ".post", "main", ".main"])

SUPPORTED_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
                 ".txt", ".md", ".markdown", ".html", ".htm", ".json", ".xml",
                 ".epub", ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif",
                 ".mp3", ".wav", ".m4a", ".flac", ".zip", ".mp4", ".mkv", ".mov"}


# ==================================================================
# 输入类型识别（移植并扩展 qiaomu 的 detect_input_type）
# ==================================================================
def detect_input(arg):
    """识别输入类型，返回 (type, 站点名/None)。"""
    s = (arg or "").strip()
    if not s:
        return "unknown", None
    if s.startswith("http://") or s.startswith("https://"):
        host = urllib.parse.urlparse(s).netloc.lower()
        # 必须先判「粘贴类」和「已移除类」：这两个集合里的域名同样出现在 SITE_RULES 的
        # 前缀匹配逻辑里（如 douyin.com 命中 .gov.cn 之外的默认分支），先判才不会误判成 url
        if any(h in host for h in PASTE_ONLY_HOSTS):
            return "paste", next((h for h in PASTE_ONLY_HOSTS if h in host), None)
        if any(h in host for h in REMOVED_HOSTS):
            return "removed", next((h for h in REMOVED_HOSTS if h in host), None)
        for k in SITE_RULES:
            if k in host:
                return {"weixin": "weixin", "podcast": "podcast", "video": "video",
                        "weibo": "weibo", "news": "news",
                        "paste": "paste"}.get(_type_of_host(k), "url"), k
        return "url", None
    if os.path.isdir(s):
        return "dir", None
    if os.path.isfile(s):
        return _file_type(s), None
    # 文件可能还不存在（UI 传来的待导入路径），按扩展名先给出预期类型
    if os.path.splitext(s)[1].lower() in SUPPORTED_EXT:
        return _file_type(s), None
    if os.path.exists(s) or os.path.exists(os.path.dirname(s)):
        return "document", None
    if re.match(r"^[\w\u4e00-\u9fff\- ]{2,60}$", s):
        return "search", None
    return "unknown", None


def _file_type(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        return "epub"
    if ext in {".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".aac"}:
        return "audio"
    if ext == ".zip":
        return "zip"
    return "document"


def _type_of_host(host):
    if host == "mp.weixin.qq.com":
        return "weixin"
    if host in ("xiaoyuzhoufm.com", "ximalaya.com"):
        return "podcast"
    if host == "bilibili.com":
        return "video"
    if host == "weibo.com":
        return "weibo"
    if host in ("toutiao.com", "baijiahao.baidu.com"):
        return "news"
    if host in PASTE_ONLY_HOSTS:
        return "paste"
    return "url"


TYPE_LABEL = {
    "weixin": "这篇微信文章", "url": "这篇文章", "video": "这个视频",
    "podcast": "这期播客", "weibo": "这条微博", "news": "这篇资讯",
    "paste": "这条分享（需粘贴）",
    "document": "这份文档", "epub": "这本书", "audio": "这段音频",
    "zip": "这个压缩包", "dir": "这个目录", "search": "这个主题",
}


# ==================================================================
# 抓取底层
# ==================================================================
def _http_get(url, ua=None, referer=None, timeout=TIMEOUT, decode="utf-8"):
    """REGPT 直连抓取（国内优先），带 UA/Referer 伪装与编码兜底。"""
    ua = ua or DEFAULT_UA
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Referer": referer or ("https://www.google.com/" if "googlebot" in ua.lower()
                               else "https://www.bing.com/"),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        elif r.headers.get("Content-Encoding") == "deflate":
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    try:
        return raw.decode(decode, errors="ignore")
    except Exception:
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                return raw.decode(enc, errors="ignore")
            except Exception:
                continue
        return raw.decode("utf-8", errors="ignore")


def _http_get_optional(url, **kw):
    """抓不到返回 None，不抛。"""
    try:
        return _http_get(url, **kw)
    except Exception:
        return None


def _rule_for(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return DEFAULT_RULE
    for k in SITE_RULES:
        if k in host:
            return SITE_RULES[k]
    return DEFAULT_RULE


# ==================================================================
# HTML → Markdown（吸收 qiaomu「一句话把难整理网页变干净」的做法）
# ==================================================================
NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "nav", "footer",
              "form", "button", "aside"]

RE_MD_HEAD = re.compile(r"^(h[1-6])\.+\s*(.*)$")
RE_IMG = re.compile(r"<img[^>]+>", re.I)
RE_A = re.compile(r"<a\s+[^>]*href=['\"]([^'\"]*)['\"][^>]*>(.*?)</a>", re.I | re.S)


def html_to_markdown(soup, bad_selectors=None, good_selectors=None):
    """去噪 + 结构化转 Markdown。没有 bs4 时用纯正则兜底。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return _html_to_md_regex(soup if isinstance(soup, str) else str(soup))

    if not hasattr(soup, "find_all"):
        soup = BeautifulSoup(str(soup), "html.parser")
    s = soup
    # 统一删除通用噪声节点（head 里的 title/meta 会污染正文，一并清掉）
    for tag in s(["script", "style", "noscript", "svg", "iframe", "form", "button",
                  "title", "meta", "link"]):
        tag.decompose()

    for sel in (bad_selectors or []):
        try:
            for node in s.select(sel):
                node.decompose()
        except Exception:
            pass

    root = s
    if good_selectors:
        for sel in good_selectors:
            try:
                node = s.select_one(sel)
            except Exception:
                node = None
            if node is not None and len(node.get_text(strip=True)) > 120:
                root = node
                break

    # 标题标签降级成 Markdown 序号，其余块级标签只留文字
    for h in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        txt = h.get_text(" ", strip=True)
        h.replace_with("\n" + "#" * int(h.name[1]) + " " + txt if txt else "\n")
    for p in root.find_all(["p", "div", "br", "li"]):
        txt = p.get_text(" ", strip=True)
        if p.name == "br":
            p.replace_with("\n")
        else:
            p.replace_with(txt)

    text = root.get_text("\n", strip=True)
    body = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        body.append(line)
    out = "\n\n".join(body)
    return _tighten(out)


def _html_to_md_regex(raw):
    s = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)</(p|div|li|h[1-6])>", "\n", s)
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = _html.unescape(s)
    lines = [l.strip() for l in s.split("\n")]
    body = [l for l in lines if l]
    return _tighten("\n\n".join(body))


def _tighten(text):
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"^[\-—=·•\s]+", "", text, flags=re.M)
    return text.strip()


def _soup(raw):
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return raw
    return BeautifulSoup(raw, "html.parser")


def _first_text(soup, selectors):
    for sel in selectors:
        try:
            node = soup.select_one(sel)
        except Exception:
            node = None
        if node:
            t = node.get_text(" ", strip=True)
            if t:
                return t
    return ""


# ==================================================================
# 各源提取器（每个源单独一套策略，这是 qiaomu 最值钱的部分）
# ==================================================================
def fetch_weixin(url):
    """微信公众号：正文在 id=js_content，标题/作者/时间在 rich_media 区块。"""
    raw = _http_get_optional(url, ua=DEFAULT_UA, referer="https://mp.weixin.qq.com/")
    if not raw or len(raw) < 500:
        raw = _http_get_optional(url, ua=CRAWLER_UA)
    if not raw:
        raise RuntimeError("微信文章抓取失败（网络不可达或被拦截）")
    s = _soup(raw)
    title = _first_text(s, ["h1.rich_media_title", "#activity-name", ".rich_media_title"])
    author = _first_text(s, [".js_author_name", "#meta_content", ".author"])
    date = _first_text(s, ["#publish_time", ".rich_media_meta_text"])
    body_node = s.select_one("#js_content") or s.select_one(".rich_media_content")
    if body_node:
        for t in body_node(["script", "style"]):
            t.decompose()
        body = body_node.get_text("\n", strip=True)
    else:
        body = html_to_markdown(s)
    meta = {"title": title, "author": author, "date": date}
    return _tighten(body), meta


def fetch_url(url, paywall=False):
    """通用网页；paywall=True 时升级为爬虫 UA + AMP + 存档兜底。"""
    raw = _http_get_optional(url)
    if not raw or len(raw) < 300:
        raw = _http_get_optional(url, ua=CRAWLER_UA)
    if not raw and paywall:
        raw = _http_get_optional(url, ua=BINGBOT_UA)
    if not raw and paywall:
        amp = _amp_of(url)
        if amp:
            raw = _http_get_optional(amp)
    if not raw:
        raise RuntimeError("网页抓取失败（国内网络不可达或站点拒绝）")
    s = _soup(raw)
    name, bad, good = _rule_for(url)
    title = _first_text(s, ["h1", "title", ".article-title", "h1.article-title",
                            ".post-title", "h2"])
    body = html_to_markdown(s, bad, good)
    if len(body) < 200:
        body = html_to_markdown(s)
    return _tighten(body), {"title": title, "site": name}


def _amp_of(url):
    """AMP 副本通常没付费墙。"""
    try:
        raw = _http_get_optional("https://amp.dev/url/https?url=" + urllib.parse.quote(url, safe=""),
                                 timeout=15)
        if raw and "ampurl=" in raw:
            m = re.search(r'ampurl=([^&"\s]+)', raw)
            if m:
                return urllib.parse.unquote(m.group(1))
    except Exception:
        pass
    return None


def fetch_bilibili(url):
    """B站：先拿字幕接口，拿不到就下音频走本地 ASR。"""
    import re as _re
    m = _re.search(r"(?:/video/|bv=|BV[0-9A-Za-z]{10})", url)
    bvid = ""
    if "BV" in url:
        bv = _re.search(r"(BV[0-9A-Za-z]{10})", url)
        bvid = bv.group(1) if bv else ""
    aid = ""
    if not bvid:
        m2 = _re.search(r"(?:av|/video/av)(\d+)", url)
        aid = m2.group(1) if m2 else ""

    info = _http_get_optional(
        "https://api.bilibili.com/x/web-interface/view?bvid=%s" % bvid if bvid else
        "https://api.bilibili.com/x/web-interface/view?aid=%s" % aid, timeout=20)
    title = ""
    if info:
        try:
            d = json.loads(info)
            dd = (d.get("data") or {})
            title = dd.get("title") or ""
        except Exception:
            pass

    # 字幕列表
    subtitle_url = ""
    if info:
        try:
            sub = (json.loads(info).get("data") or {}).get("subtitle") or {}
            for it in (sub.get("list") or []):
                if it.get("lan") in ("zh-CN", "zh-Hans", "zh"):
                    subtitle_url = it.get("url") or ""
                    break
            if not subtitle_url and (sub.get("list") or []):
                subtitle_url = sub["list"][0].get("url") or ""
        except Exception:
            pass
    if subtitle_url:
        raw = _http_get_optional(subtitle_url)
        if raw:
            try:
                d = json.loads(raw)
                body = "\n".join(
                    (seg.get("text") or "").replace("\n", " ")
                    for seg in (d.get("body") or []))
                if body.strip():
                    return _tighten(body), {"title": title, "mode": "字幕接口"}
            except Exception:
                pass
    # 兜底：本地 ASR
    return _fetch_by_local_asr(url, title, hint="B站")


def fetch_weibo(url):
    """微博：整页是登录墙，改用移动版详情接口 m.weibo.cn/statuses/show 拿正文。"""
    m = re.search(r"(?:/statuses/|statuses/show\?id=|id=)(\d{8,20})", url)
    if not m:
        raise RuntimeError("识别不出微博 ID；把微博链接整条贴进来（m.weibo.cn 或 www.weibo.com/.../status/...）")
    raw = _http_get_optional("https://m.weibo.cn/statuses/show?id=%s" % m.group(1), timeout=20)
    if not raw:
        raise RuntimeError("微博详情接口不可达（可能需要登录态）")
    try:
        d = json.loads(raw).get("data") or {}
        body = _html.unescape(re.sub(r"<br\s*/?>", "\n", d.get("text") or ""))
        title = d.get("user", {}).get("name") or ""
        if body:
            return _tighten(re.sub(r"<[^>]+>", "", body)), {"title": title or _guess_title(url)}
    except Exception:
        pass
    return fetch_url(url)


def fetch_podcast(url):
    """小宇宙 / 喜马拉雅：优先平台字幕，否则下音频走本地 ASR。"""
    info = _http_get_optional(url + "?from=sp", timeout=20) or _http_get_optional(url, timeout=20)
    title = ""
    if info:
        s = _soup(info)
        title = _first_text(s, ["h1", ".title", "#enclosure"])
    return _fetch_by_local_asr(url, title, hint="播客")


def _fetch_by_local_asr(url, title, hint=""):
    """下载音频 → 本地 ASR 转写。拿不到音频就给可执行的下一步。"""
    try:
        import asr_listen
    except Exception as e:
        raise RuntimeError("%s：本机未启用本地语音转写（%s），可先把音频文件放进「待转写」目录再导入" %
                           (hint or "音频", e))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        audio = os.path.join(td, "src.bin")
        if not _download_audio(url, audio):
            raise RuntimeError("%s：没能抓到音频地址（平台有防盗链）。可手动把 mp3/m4a 放进待转写目录，"
                               "再用「多源导入台 → 本地文件」导入。" % (hint or "音频"))
        text = asr_listen.transcribe(audio)
    if not text or len(text.strip()) < 20:
        raise RuntimeError("%s：已下载音频但本地转写结果为空（可能模型未装或音频过长）。" % (hint or "音频"))
    return _tighten(text), {"title": title, "mode": "本地语音转写"}


def _download_audio(url, out_path):
    """从页面里挖直链音频（mp3/m4a/mpeg/m4s），搜不到就返回 False。"""
    try:
        raw = _http_get_optional(url, timeout=20)
        if not raw:
            return False
        m = re.findall(r"https?://[^\"'\s<>]+\.(?:mp3|m4a|aac|wav)", raw)
        if m:
            data = _http_get_optional(m[0].replace("http://", "https://") or m[0], timeout=90)
            if data:
                with open(out_path, "wb") as f:
                    f.write(data)
                return True
    except Exception:
        pass
    return False


def fetch_audio_file(path):
    """本地音频文件 → 本地 ASR。"""
    import asr_listen
    text = asr_listen.transcribe(path)
    return _tighten(text or ""), {"title": os.path.basename(path)}


def fetch_epub(path):
    """EPUB：ebooklib 优先，无依赖时退 zip+xml。"""
    try:
        import ebooklib
        from ebooklib import epub as epub_lib
        book = epub_lib.read_epub(path)
        parts = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                node = _soup(item.get_content())
                parts.append(node.get_text("\n", strip=True) if hasattr(node, "get_text")
                             else str(node))
        if parts:
            return _tighten("\n\n".join(parts)), {"title": os.path.splitext(os.path.basename(path))[0]}
    except Exception:
        pass
    import zipfile
    out = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.lower().endswith((".xhtml", ".html", ".htm")):
                try:
                    node = _soup(z.read(n).decode("utf-8", errors="ignore"))
                    t = node.get_text("\n", strip=True) if hasattr(node, "get_text") else ""
                    if t:
                        out.append(t)
                except Exception:
                    pass
    if not out:
        raise RuntimeError("EPUB 解析失败（缺少 ebooklib，可 pip install ebooklib）")
    return _tighten("\n\n".join(out)), {"title": os.path.splitext(os.path.basename(path))[0]}


def fetch_zip(path):
    """ZIP：解压后交回文件批量管道。"""
    import zipfile
    import doc_ingest
    td = os.path.join(os.path.dirname(os.path.abspath(path)), "_qm_unzip",
                      os.path.splitext(os.path.basename(path))[0] + "_" +
                      str(int(time.time())))
    os.makedirs(td, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extractall(td)
    files = []
    for root, dirs, names in os.walk(td):
        dirs[:] = [d for d in dirs if d not in doc_ingest.SKIP_DIR_NAMES]
        for n in names:
            if os.path.splitext(n)[1].lower() in SUPPORTED_EXT:
                files.append(os.path.join(root, n))
    if not files:
        raise RuntimeError("压缩包里没有可解析的文件")
    return "\n\n".join("## %s\n%s" % (os.path.basename(f), doc_ingest.extract(f)[0])
                       for f in files), {"title": os.path.basename(path), "files": len(files)}


def _web_search(keyword, limit=5):
    """国内搜索兜底：必应 → 百度。原项目的 Google / r.jina.ai 在国内都不可达，
    这里换成国内搜索引擎，只取标题+摘要作为「检索线索」，正文仍由本地库与直连抓取负责。"""
    out = []
    for base in ("https://cn.bing.com/search?q=", "https://www.baidu.com/s?wd="):
        raw = _http_get_optional(base + urllib.parse.quote(keyword), timeout=20)
        if not raw:
            continue
        s = _soup(raw)
        for node in s.find_all("li") if hasattr(s, "find_all") else []:
            t = node.get_text(" ", strip=True) if hasattr(node, "get_text") else ""
            if not t or len(t) < 12:
                continue
            link = ""
            try:
                a = node.find("a", href=True) if hasattr(node, "find") else None
                link = a.get("href") if a else ""
            except Exception:
                link = ""
            if "bing.com" in link or "baidu.com" in link or not link:
                link = ""
            out.append({"title": t[:120], "url": link})
            if len(out) >= limit:
                return out
    return out


def fetch_search(keyword):
    """关键词 → 本地知识库优先；本地没命中再走国内搜索引擎（必应/百度）。"""
    try:
        import knowledge_base
        res = knowledge_base.hybrid_query(keyword, name="myme_kb", top_k=8)
    except Exception as e:
        res = []
        parts = ["【本地知识库检索失败】%s" % e]
    if not res:
        hits = _web_search(keyword)
        if hits:
            parts = ["【本机知识库没命中，以下为国内搜索线索（可直接按链接回填导入）】"]
            for i, h in enumerate(hits, 1):
                parts.append("%d. %s\n%s" % (i, h["title"], h["url"] or "（无直达链接）"))
            return _tighten("\n\n".join(parts)), {"title": keyword, "hits": len(hits),
                                                  "mode": "国内搜索"}
    parts = []
    for i, r in enumerate(res, 1):
        label = r.get("label") or r.get("source") or "来源%d" % i
        bc = r.get("breadcrumb")
        parts.append("【片段%d · %s%s】\n%s" % (
            i, (bc + " · " if bc else "") + label, "",
            (r.get("text") or "")[:1800]))
    return _tighten("\n\n".join(parts)), {"title": keyword, "hits": len(res),
                                          "mode": "本地知识库" if res else "国内搜索未命中"}


# ==================================================================
# 入库（替换 notebooklm 的 upload）
# ==================================================================
def ingest_text(title, text, kb="myme_kb", category=""):
    """把抓来的正文分块→向量化→并入本地知识库。返回与 ingest_document 同构的统计。"""
    import knowledge_base as kbmod
    import doc_ingest

    text = doc_ingest.clean_text(text or "")
    if not text.strip():
        return {"added": 0, "total": 0, "name": title, "skipped": "empty"}
    h = "qm_%s" % _sha1(text)
    meta_doc = {"name": title, "source": title, "file": title, "type": "multi-source",
                "date": time.strftime("%Y-%m-%d"), "status": "current"}
    chunks = kbmod.chunk_text(text, meta=meta_doc)
    if getattr(kbmod, "KB_PARENT_CHILD", "0") == "1":
        try:
            parents, kids = kbmod.kb_chunking.split_parent_child(text)
            chunks = kids or chunks
        except Exception:
            pass
    for c in chunks:
        c["embedding"] = kbmod.ollama_embed(kbmod.embed_text(c), model=kbmod.EMBED_MODEL)
        c["src"] = h
    store_path = kbmod._store_path(kb)
    if os.path.exists(store_path):
        store = kbmod.load_kb(kb)
    else:
        store = {"name": kb, "model": kbmod.EMBED_MODEL, "chunks": [], "sources": {}}
    sources = store.setdefault("sources", {})
    if h in sources and sources[h].get("n") == len(chunks):
        return {"added": 0, "removed": 0, "total": len(store["chunks"]),
                "name": title, "skipped": "unchanged"}
    old = store["chunks"]
    store["chunks"] = [c for c in old if c.get("src") != h]
    removed = len(old) - len(store["chunks"])
    store["chunks"].extend(chunks)
    sources[h] = {"name": title, "file": title, "type": "multi-source", "n": len(chunks)}
    kbmod._save_store(store)
    return {"added": len(chunks), "removed": removed, "total": len(store["chunks"]),
            "name": title}


def _sha1(s):
    import hashlib
    return hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


# ==================================================================
# 主入口：一句自然语言 / 一个链接 / 一个文件 → 清洗 → 本地知识库
# ==================================================================
def run(arg, kb="myme_kb", category="", asr=False, deep=False):
    """统一搬运入口。返回 dict：
    {type, site, title, chars, ingest, ok, error, warn}"""
    t0 = time.time()
    typ, site = detect_input(arg)
    out = {"input": arg, "type": typ, "site": site, "ok": False, "title": "",
           "chars": 0, "ingest": None, "secs": 0.0, "warn": ""}

    try:
        if typ == "weixin":
            text, meta = fetch_weixin(arg)
        elif typ == "video":
            text, meta = fetch_bilibili(arg)
        elif typ == "podcast":
            text, meta = fetch_podcast(arg)
        elif typ == "weibo":
            text, meta = fetch_weibo(arg)
        elif typ == "removed":
            raise RuntimeError("该源已移除：原项目依赖的境外平台在国内网络不可达。"
                               "请改投国内同源（视频走 B站、短内容走微博/公众号，"
                               "或把本地已下载的文件放进目录批量导入）")
        elif typ == "paste":
            raise RuntimeError("该内容必须登录后才能在客户端里看全文，抓不到。"
                               "复制分享文案/正文后，用「粘贴正文」入口粘贴导入")
        elif typ in ("url", "news"):
            text, meta = fetch_url(arg, paywall=True)
        elif typ == "epub":
            text, meta = fetch_epub(arg)
        elif typ == "zip":
            text, meta = fetch_zip(arg)
        elif typ == "audio":
            if not asr:
                out["warn"] = "音频文件需要转写，加 --asr 或勾选本地转写"
            text, meta = fetch_audio_file(arg) if asr else ("", {})
        elif typ == "dir":
            import doc_ingest
            # ⚠ iter_files 返回 [(路径, 扩展名), ...] 列表，不是 (列表, 计数) 二元组
            files = doc_ingest.iter_files(arg)
            if not files:
                raise RuntimeError("目录里没有可解析的文件")
            parts = []
            for f, _ext in files:          # iter_files 给的是 (路径, 扩展名)
                try:
                    parts.append("## %s\n%s" % (os.path.basename(f), doc_ingest.extract(f)[0]))
                except Exception:
                    pass
            text, meta = "\n\n".join(parts), {"title": os.path.basename(arg.rstrip("/\\")),
                                              "files": len(files)}
        elif typ == "document":
            import doc_ingest
            raw, meta = doc_ingest.extract(arg)
            text = raw
            category = category or "本地文档"
        elif typ == "search":
            text, meta = fetch_search(arg)
        else:
            raise RuntimeError("识别不出输入类型，请给：网页链接 / 本地文件 / 文件夹")
    except RuntimeError as e:
        out["error"] = str(e)
        out["secs"] = round(time.time() - t0, 1)
        return out
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        out["secs"] = round(time.time() - t0, 1)
        return out

    out["title"] = (meta or {}).get("title") or _guess_title(arg)
    out["chars"] = len(text or "")
    if not (text or "").strip():
        out["error"] = "抓取到空内容（站点可能改为动态加载，建议换镜像或用本地文件）"
        out["secs"] = round(time.time() - t0, 1)
        return out
    # 拼成带出处的文档头，入库后回溯可看到是哪一天从哪个链接搬来的
    doc = "# %s\n\n> 来源：%s\n> 时间：%s\n\n---\n\n%s" % (
        out["title"], arg, time.strftime("%Y-%m-%d %H:%M"), text)
    if (meta or {}).get("mode"):
        out["warn"] = "提取方式：%s" % meta["mode"]
    # ⚠ 关键词检索是「只读查库」：内容是库里已有的，再回灌一遍等于每问一次就给库
    # 塞几段重复块，问多了库会无声膨胀、检索还会互相污染。所以这一类只回显不入库。
    if typ == "search" and not deep:
        try:
            import knowledge_base
            st = knowledge_base.load_kb(kb) or {}
            out["ingest"] = {"added": 0, "removed": 0,
                             "total": len(st.get("chunks") or []), "name": kb,
                             "skip": "检索只读，结果不回灌"}
        except Exception as e:
            out["ingest"] = {"added": 0, "removed": 0, "total": 0, "name": kb,
                             "skip": "只读模式（%s）" % e}
        out["ok"] = True
        out["secs"] = round(time.time() - t0, 1)
        return out
    try:
        out["ingest"] = ingest_text(out["title"], doc, kb=kb, category=category)
    except Exception as e:
        out["error"] = "入库失败：%s" % e
        out["secs"] = round(time.time() - t0, 1)
        return out
    out["ok"] = bool(out["ingest"].get("added"))
    out["secs"] = round(time.time() - t0, 1)
    return out


def _guess_title(arg):
    if "http" in arg:
        try:
            return urllib.parse.urlparse(arg).netloc + (urllib.parse.urlparse(arg).path or "")[:40]
        except Exception:
            pass
    if os.path.isfile(arg) or os.path.isdir(arg):
        return os.path.basename(arg.rstrip("/\\"))
    return (arg or "")[:40]


# ==================================================================
# 三轮递进深度提问（吸收 qiaomu 的 deep-analysis 提示词工程）
# ==================================================================
def deep_questions(typ):
    name = TYPE_LABEL.get(typ, "这份内容")
    r1 = [
        "请用一段话概括%s的核心主题与目的，只依据资料内容回答，不要联网搜索。" % name,
        "%s的整体结构是怎样的？按章节或逻辑模块逐一列出，每模块 2-3 句概括。" % name,
        "%s 提出了哪些核心论点？逐条列出并用原文语句支撑。" % name,
        "%s 中最有信息量或反常识的内容是什么？列出 3-5 条并说明为什么。" % name,
    ]
    if typ in ("epub", "document"):
        r2 = [
            "拆解%s的前提假设、推理过程与最终结论，并引用具体段落。" % name,
            "%s 引用了哪些关键案例、数据或文本证据？逐个说明它们在论证中的作用。" % name,
            "%s 是否存在内部矛盾或可商榷之处？若有，分析根源；若没有，说明为何论证站得住。" % name,
            "如果要对%s 提出一个最尖锐的批评，会是什么？从论证完整性、证据充分性、视角局限三个角度。" % name,
            "%s 最独特的洞察是什么？一句话概括，并说明它为什么重要。" % name,
        ]
    elif typ in ("video", "audio"):
        r2 = [
            "这个视频的核心论点与支撑论据是什么？拆解其论证结构。",
            "视频提到了哪些具体案例、数据或研究？逐个说明作用。",
            "讲述者的立场是否偏向？哪些观点可能经不起推敲？",
            "这个视频里别处看不到的独特信息是什么？",
            "请一位持反对立场的专家回应，他最可能提出的三个反驳点是什么？",
        ]
    else:
        r2 = [
            "拆解%s的叙事或论证结构：开头如何立框架，中间如何展开，结尾如何收束。" % name,
            "%s 引用了哪些关键案例、数据或引用？评估其可信度与相关性。" % name,
            "%s 的视角是否存在局限？有没有未被讨论的维度？" % name,
            "%s 中最有冲击力的一个观点是什么？为什么？" % name,
            "给作者写一封反馈信，你会提哪三条建设性意见？",
        ]
    r3 = [
        "读者读完之后最该带走的一个认知改变是什么？哪些观点会颠覆既有认知？",
        "从%s 中可提取哪些可操作的行动指南或决策原则？列出 3-5 条。" % name,
        "用三个最有力的理由说服一个没接触过它的人去认真看，每个理由一句话。",
    ]
    return [("第一轮：概览与框架", r1), ("第二轮：深度挖掘", r2), ("第三轮：综合与反刍", r3)]


def deep_analyze(arg, kb="myme_kb", rounds=None):
    """三轮递进：每轮用当前知识库检索作答（轮间把上一轮问答并进上下文）。"""
    import kb_qa
    typ, _ = detect_input(arg)
    qs = rounds or deep_questions(typ)
    all_q, all_a = [], []
    hist = []
    for label, items in qs:
        for q in items:
            r = kb_qa.answer(q, names=[kb], expand=False, max_chars=5000)
            a = r.get("answer") or ""
            all_q.append(q)
            all_a.append(a)
            if a:
                hist.append((q, a))
    return {"title": _guess_title(arg), "rounds": len(qs),
            "questions": all_q, "answers": all_a,
            "total_questions": len(all_q),
            "answered": len([x for x in all_a if x])}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="多源搬运工（国内离线版）→ 本地向量知识库")
    ap.add_argument("arg", help="网页链接 / 微信文章 / 音频 / 文件 / 目录 / 关键词")
    ap.add_argument("--kb", default="myme_kb")
    ap.add_argument("--category", default="多源导入")
    ap.add_argument("--asr", action="store_true", help="音频走本地语音转写")
    ap.add_argument("--deep", action="store_true", help="三轮递进深度解读（先入库再问）")
    a = ap.parse_args()
    if a.deep:
        r = deep_analyze(a.arg, kb=a.kb)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:4000])
        return
    r = run(a.arg, kb=a.kb, category=a.category, asr=a.asr)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
