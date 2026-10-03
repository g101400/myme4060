# -*- coding: utf-8 -*-
"""
myme.app — 本地「PPT制作及分身演示」Web 应用（纯标准库 http.server，无需 gradio）
功能：
  ① 准备工作：上传 PPT → 解析 → 增量并入知识库 → 用当前分身的声音逐页讲解（中英）
  ② 智能答疑：打字或传语音 → 混合检索知识库(向量+BM25) → 分身声音回答（中英）
  ③ 数字分身视频：输入台词 → 声音 + 口型驱动的形象，输出 MP4
  ④ 知识库：上传笔记/Word(.txt/.md/.docx) 增量入库，查看已收录来源
  ⑤ 正式演示：分身肖像 + PPT 原稿逐页同步播放，自动翻页
  ⑥ 答疑总结：沉淀问答、生成《答疑汇总》、导出、入库
  ⑦ 资源管理 / ⑧ 导入播放文件 / ⑨ 制作 PPT（大模型生成大纲并落盘 .pptx）
大模型接入（知识库/答疑/制作PPT）由「设置 → 大模型接入」统一配置（本地 Ollama 或线上 OpenAI-compatible）。
PWA：manifest.json(display=standalone) + service worker 离线壳 + 图标，可"添加到主屏"。
运行：python app.py  （完整版默认 http://127.0.0.1:7860，播放版默认 7861）
说明：上传走 base64+JSON；音频经 /api/audio 流式返回。数据全程本地，不出机。
"""
import os
import sys
import json
import time
import re
import base64
import tempfile
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import http.server
import threading
import zlib
import struct
import uuid
import config
from config import (VOICE_SAMPLE, PERSONA, EMBEDDED_PY, APP_NAME,
                    RETRIEVE_ALPHA, RETRIEVE_THRESHOLD, RETRIEVE_TOP_K,
                    TTS_SERVICE_URL, TTS_SERVICE_PORT, USE_TTS_SERVICE,
                    AVATAR_SERVICE_URL, AVATAR_SERVICE_PORT, USE_AVATAR_SERVICE,
                    AVATAR_IMAGE, AVATAR_IMAGE_DEFAULT, AVATAR_RES, LIVEPORTRAIT_REPO, FFMPEG,
                    AVATAR_MOTION_DEFAULT,
                    AVATAR_ENGINE, SONIC_URL, SONIC_CHECKPOINT, SONIC_UNET,
                    SONIC_DTYPE, SONIC_MIN_RES, SONIC_STEPS, SONIC_FPS, SONIC_TIMEOUT,
                    resolve_avatar, list_portraits, TTS_SEED_BASE, resolve_voice,
                    WORKSPACE, DATA_DIR, PLAYBACK_MODE, PORT_FULL, PORT_PLAYBACK,
                    get_current_persona,
                    DIR_BACKUP, BACKUP_DECK_EXT, BACKUP_DOC_EXT)
import pptx_parse, knowledge_base, qa_brain, narrate, avatar
import avatar_sonic  # #382 中期方案：扩散式 talking-head（Sonic / ComfyUI），失败自动回退 LivePortrait
import doc_parse, persons
import llm_client
import pptx_write
import ppt_templates, doc_write
import app_settings
import make_ppt
import shutil
import zipfile

KB_NAME = "myme_kb"
# 完整版与播放版采用不同端口：完整版默认 7860，播放版默认 7861（可同时运行互不冲突）。
PORT = int(os.environ.get("MYME_PORT",
                          str(PORT_PLAYBACK if PLAYBACK_MODE else PORT_FULL)))
HERE = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# PWA 静态资源（图标用纯标准库生成 PNG）
# ============================================================
def _make_png(path, size=192, rgb=(46, 125, 255)):
    w = h = size
    raw = bytearray()
    row = bytes(rgb) * w
    for _ in range(h):
        raw.append(0)
        raw.extend(row)
    comp = zlib.compress(bytes(raw), 9)
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def _ensure_pwa_assets():
    for s in (192, 512):
        p = os.path.join(HERE, f"icon-{s}.png")
        if not os.path.exists(p):
            _make_png(p, size=s)


MANIFEST = json.dumps({
    "name": "PPT制作及分身演示",
    "short_name": "PPT分身",
    "description": "本地 PPT 制作与分身演示：用你的声音讲 PPT 并自动答疑",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0f1420",
    "theme_color": "#0f1420",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    ],
}, ensure_ascii=False)

SW_JS = """const CACHE='myme-v5';
const SHELL=['/','/manifest.json','/icon-192.png','/icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim();});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(CACHE).then(c=>c.put(e.request,cp));return r;}).catch(()=>caches.match(e.request).then(m=>m||caches.match('/'))));
});
"""


def _load_index_html():
    """页面模板放在 ui/index.html（UI 规模变大后独立成文件，便于维护与版本管理）。

    找不到文件时回退到极简提示页，保证服务仍能启动。
    """
    fp = os.path.join(HERE, "ui", "index.html")
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<body style='font-family:sans-serif;padding:40px'>"
            "界面模板缺失：ui/index.html</body></html>")


INDEX_HTML = _load_index_html()


# ============ 异步任务（PPT 讲解）============
# 背景：整册 PPT 讲解是长任务（LLM 逐页写稿 + TTS 逐页合成，约 1~2 分钟/页）。
# 早期是同步 POST：浏览器空等二三十分钟且无任何反馈，表现为"点了没反应"。
# 现改为：POST 立即返回 job_id，后台线程执行，前端轮询 /api/progress 看到逐页进度。
JOBS = {}
JOBS_LOCK = threading.Lock()


def _job_new(total=0):
    jid = "%d" % int(time.time() * 1000)
    with JOBS_LOCK:
        JOBS[jid] = {"id": jid, "stage": "排队中", "page": 0, "total": total,
                     "done": False, "error": None, "narrations": [],
                     "full_audio": None, "summary": None, "started": time.time()}
    return jid


def _job_get(jid):
    with JOBS_LOCK:
        return JOBS.get(jid)


def _job_update(jid, **kw):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j.update(kw)


def _job_worker(jid, tmp_path, lang, words, narr_map=None, narr_stats=None,
                version_id=None, version_name=None, person=None):
    """后台线程：解析 → 入库 → 逐页定稿 → 逐页合成语音 → 合并全文。

    讲解文本是可选的：narr_map 为 {页码: 讲解词}
      - 该页有讲稿 → 直接采用（不调 LLM，秒出且完全按用户口径）
      - 该页没有   → 按幻灯片内容自动生成（部分页缺失也能自动补齐）
    narr_map 为空 = 用户没传讲稿，整篇按幻灯片自动生成。
    version_id 给定时：结果写入该独立版本（不存在则新建），不覆盖当前演示态——
    否则重跑同一份 PPT 会把 default 的页与 narr-XX.wav 全部覆盖掉。
    """
    try:
        _job_update(jid, stage="解析 PPT 与并入知识库…")
        slides = pptx_parse.parse(tmp_path)
        # 缓存页内容，供前端"讲解时同步展示该页"与"答疑定位相关页"
        global SLIDES
        SLIDES = _slides_to_urls(slides)
        SLIDES_META.update({"source": os.path.basename(tmp_path), "count": len(SLIDES),
                            "source_path": tmp_path})
        _save_slides()
        # 原稿版式页图（soffice→PDF→MuPDF 光栅化）；失败不致命，前端回退 HTML 版式
        _render_slide_images(tmp_path, jid=jid)
        kb = knowledge_base.ingest_pptx(tmp_path, KB_NAME)
        _job_update(jid, stage="生成讲解", total=len(slides), page=0)

        results = []
        wav_paths = []
        n_used = n_gen = 0
        narr_map = narr_map or {}
        for i, sl in enumerate(slides, 1):
            idx = sl.get("idx", i)
            text = narr_map.get(idx) or narr_map.get(i) or ""
            if text:
                n_used += 1
                _job_update(jid, stage="第 %d/%d 页：采用讲稿文本" % (i, len(slides)),
                            page=i - 1)
            else:
                n_gen += 1
                _job_update(jid, stage="第 %d/%d 页：自动生成讲稿" % (i, len(slides)),
                            page=i - 1)
                from qa_brain import generate_narration
                text = generate_narration(sl, lang=lang, words=words, model="qwen3.5:9b")
            _job_update(jid, stage="第 %d/%d 页：合成%s语音" % (i, len(slides), _cur_person_label(person)))
            # 独立版本用专属音频前缀，避免覆盖 default 的 narr-XX.wav
            wav_prefix = ("narr_%s_" % version_id) if version_id else "narr-"
            out = os.path.join(config.DIR_NARR, "%s%02d.wav" % (wav_prefix, sl["idx"]))
            ok = narrate.synth_one(text, lang, out, seed=TTS_SEED_BASE + i,
                                   ref=resolve_voice(person))
            item = {"idx": sl["idx"], "title": sl.get("title", ""),
                    "narration": text,
                    "audio": ("/api/audio?name=" + os.path.basename(out)) if ok else None}
            results.append(item)
            if ok:
                wav_paths.append(out)
            # 每完成一页就推送，前端可先听已完成的页
            with JOBS_LOCK:
                j = JOBS.get(jid)
                if j is not None:
                    j["narrations"] = list(results)
                    j["page"] = i

        _job_update(jid, stage="合并全篇音频…")
        full_name = ("full_%s.wav" % version_id) if version_id else "full_narration.wav"
        full = narrate.concat_audio(wav_paths,
                                    os.path.join(config.DIR_NARR, full_name))
        # 把逐页讲解（音频URL + 文本）挂回 SLIDES，便于"正式演示"按页播放与刷新后恢复
        narr_map = {item["idx"]: item for item in results}
        for sl in SLIDES:
            it = narr_map.get(sl.get("idx"))
            if it:
                sl["audio"] = it["audio"] if it.get("audio") else None
                sl["narration"] = it["narration"]
        full_url = "/api/audio?name=full_narration.wav" if full else None
        # —— 独立版本落库：给 version_id 时结果只写入该版本，不污染当前演示态 ——
        if version_id:
            with JOBS_LOCK:
                ver = VERSIONS.get(version_id)
                if ver is None:
                    VERSIONS[version_id] = {
                        "name": version_name or version_id,
                        "created": time.strftime("%Y-%m-%d %H:%M"),
                        "slides": [], "audio_full": None}
                    SLIDES_META.setdefault("versions", []).append(
                        {"id": version_id, "name": version_name or version_id,
                         "created": VERSIONS[version_id]["created"]})
                    if version_id not in VERSION_ORDER:
                        VERSION_ORDER.append(version_id)
                VERSIONS[version_id]["slides"] = [dict(s) for s in SLIDES]
                VERSIONS[version_id]["audio_full"] = full_url
                SLIDES_META["currentVersion"] = version_id
        _save_slides()
        ok_n = len([r for r in results if r["audio"]])
        src_note = ("（讲稿来源：%d 页采用讲解文本，%d 页按幻灯片自动生成）"
                    % (n_used, n_gen)) if (n_used or n_gen) else ""
        _job_update(
            jid, stage="完成", done=True, page=len(slides),
            summary=("解析 %d 页，知识库并入 %d 段（共 %d 段），生成 %d 条%s语音讲解。%s"
                     % (len(slides), kb["added"], kb["total"], ok_n, lang, src_note)),
            full_audio=("/api/audio?name=" + full_name) if full else None,
        )
    except Exception as e:
        import traceback
        _job_update(jid, done=True, error=str(e), stage="失败",
                    note=traceback.format_exc()[-400:])


# ============ 语言识别与关联页定位 ============
_LANG_FORCE = {"中文": "中文", "英文": "English", "English": "English", "Chinese": "中文"}


def _detect_lang(text):
    """极简语种判定：拉丁字母占比高判为英文，否则中文。用于提示模型"先理解再作答"。"""
    if not text:
        return "中文"
    letters = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "英文" if (letters > cjk and letters > 3) else "中文"


def _find_related_slide(question, ctx_res):
    """从检索结果里定位最相关的 PPT 页码；无 PPT 或未命中则返回 None。"""
    if not SLIDES:
        return None
    best, best_score = None, 0.0
    for r in (ctx_res or [])[:8]:
        meta = r.get("meta") or {}
        idx = meta.get("idx") or meta.get("slide") or meta.get("page")
        if idx is None:
            # 从 label 里兜底提取"第N页"
            import re as _re
            m = _re.search(r"第\s*(\d+)\s*页", r.get("label", "") or "")
            idx = int(m.group(1)) if m else None
        if idx is None:
            continue
        score = float(r.get("score") or r.get("vec") or 0.0)
        # 标签/标题命中额外加权
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if score >= best_score:
            best_score, best = score, idx
    if best is None:
        return None
    for s in SLIDES:
        if s["idx"] == best:
            return s
    return {"idx": best, "title": f"第 {best} 页", "text": "", "notes": "", "images": []}


# ============ 设置持久化（音量/缩放/分身尺寸位置/字体/主题等）============
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DEFAULT_SETTINGS = {
    "volume": 1.0, "muted": False,
    "uiScale": 1.0, "fontSize": 15, "fontColor": "#1f2937",
    "theme": "light", "accent": "#1d4ed8",
    "avatarScale": 1.0, "avatarX": 50, "avatarY": 50,
    "lang": "中文", "person": None, "avatar": None,
    "presPos": 0,    # 正式演示断点：领导打断/结束时的页码，下次从这儿续播
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def save_settings(s):
    cur = load_settings()
    cur.update(s or {})
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return cur


def _artifact(kind, path):
    """把产物文件包装成前端可展示/可下载的条目。"""
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    return {"kind": kind, "name": os.path.basename(path), "path": path, "size": size,
            "download": "/api/download?name=" + os.path.basename(path)}


# ============ v2.6 书面文档生成（与 PPT 内容不同：要点 → 成文）============
_WRITTEN_DOC_SYSTEM = (
    "你是资深中文公文/汇报材料起草人。给定一份 PPT 的要点提纲，"
    "把它扩写成可直接打印阅读的【书面汇报稿】。"
    "要求：\n"
    "1. 书面稿与 PPT 分工不同——PPT 只放要点，书面稿必须写实："
    "交代背景与目标、写清做了什么与怎么做的、给出数据与佐证、"
    "说明问题与不足、提出下一步计划；每个小标题下至少一段完整段落，"
    "不要只剩短语列表。\n"
    "2. 语气正式、逻辑连贯、可用「一是/二是/三是」「综上」等公文连接词，"
    "但不要空话套话堆砌。\n"
    "3. 只能依据给定提纲与已知事实展开；确实没有依据的数据一律写"
    "「【待补充】」，严禁编造数字、日期、人名。\n"
    "4. 只输出 JSON，结构为 "
    "{\"blocks\":[{\"type\":\"h1|h2|p|bullets|quote\",\"text\":\"\",\"items\":[]}, ...]}，"
    "其中 h1 用于一级章节，h2 用于二级小节，p 为正文段落，"
    "bullets 的 items 为要点数组，quote 用于摘要导语。不要输出代码块围栏与解释。"
)


def _gen_written_doc(topic, slides, audience="", scene="", industry="",
                     lang="中文", use_kb=True):
    """把 PPT 要点扩写成书面文档 blocks。

    优先用大模型成文；模型不可用/返回不合规时回落离线骨架
    （doc_write.blocks_from_slides），保证任何情况下都能出一份稿子。
    """
    # ---- 尝试大模型成文 ----
    try:
        brief = json.dumps(
            [{"title": s.get("title", ""), "bullets": s.get("bullets", [])}
             for s in (slides or [])], ensure_ascii=False)[:6000]
        user = ("主题：%s\n" % topic)
        if industry:
            user += "行业/领域：%s\n" % industry
        if scene:
            user += "使用场景：%s\n" % scene
        if audience:
            user += "读者对象：%s\n" % audience
        user += "语言：%s\n\nPPT 要点提纲：\n%s" % (lang, brief)
        txt, _model = app_settings.chat(
            [{"role": "system", "content": _WRITTEN_DOC_SYSTEM},
             {"role": "user", "content": user}],
            cap="text", max_tokens=3000)
        data = _extract_json(txt)
        blocks = data.get("blocks") if isinstance(data, dict) else None
        blocks = [b for b in (blocks or [])
                  if isinstance(b, dict) and (b.get("text") or b.get("items"))]
        if len(blocks) >= 3:
            return blocks
    except Exception:
        pass
    # ---- 回落：离线骨架 ----
    return doc_write.blocks_from_slides(
        slides, meta={"theme": topic, "date": time.strftime("%Y-%m-%d")})


def _extract_json(text):
    """从大模型输出里抠出第一个 JSON 对象（容忍前后废话与代码块围栏）。"""
    if isinstance(text, dict):
        return text
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    # JSON5 风格容错：去除尾随逗号
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", s[i:j + 1]))
    except Exception:
        return {}


# ============ v2.6.1 根据知识库生成文字材料（总结报告 / 半年汇报 / …）============
# 与「制作 PPT」的书面文档不同：这里不依赖 PPT 提纲，而是直接拿【知识库检索到的素材】
# 按所选模板（总结报告 / 半年汇报 / 工作总结 / 项目汇报 / 季度汇报 / 自定义）起草成文。
_KB_DOC_TEMPLATES = {
    "summary":   {"label": "总结报告", "query": "工作成果 进展 完成情况 数据 指标 问题 不足 下一步 计划 总结",
                  "sections": "背景与目标、主要工作与成效、关键数据与佐证、存在问题与不足、下一步工作计划"},
    "halfyear":  {"label": "半年汇报", "query": "上半年 工作 成果 数据 指标 进展 问题 下半年 计划",
                  "sections": "上半年工作概述、重点工作与成果、关键指标与数据、存在问题、下半年工作计划"},
    "work":      {"label": "工作总结", "query": "全年 工作 业绩 成果 经验 不足 展望 总结",
                  "sections": "工作回顾、主要业绩与成果、经验体会与不足、未来展望"},
    "project":   {"label": "项目汇报", "query": "项目 背景 目标 实施 过程 成果 风险 进度 下一步",
                  "sections": "项目背景与目标、实施过程、主要成果、风险与问题、下一步安排"},
    "quarter":   {"label": "季度汇报", "query": "本季度 工作 关键指标 问题 改进 下季度 重点",
                  "sections": "本季度工作概述、关键指标与数据、问题与改进、下季度重点"},
    "custom":    {"label": "自定义", "query": "", "sections": ""},
}
_KB_DOC_SYSTEM = (
    "你是资深中文公文/汇报材料起草人。下面会给你【文字材料类型】与【知识库检索到的素材】，"
    "请据此起草一份可直接打印阅读的书面汇报材料。\n"
    "要求：\n"
    "1. 严格按给定的章节结构组织，每个章节下至少一段完整段落，不要只剩短语列表；"
    "数据、事实、人名、日期一律以知识库素材为准。\n"
    "2. 素材里没有提供的内容写「【待补充】」，严禁编造数字、日期、人名。\n"
    "3. 语气正式、逻辑连贯，可用「一是/二是/三是」「综上」等公文连接词，但不堆砌空话套话。\n"
    "4. 只输出 JSON：{\"title\":\"\",\"blocks\":[{\"type\":\"h1|h2|p|bullets|quote\","
    "\"text\":\"\",\"items\":[]}]}，h1 用于一级章节，h2 用于二级小节，p 为正文段落，"
    "bullets 的 items 为要点，quote 用于摘要导语。不要输出代码块围栏与解释。"
)


def _gen_kb_doc(template, lang="中文", topic="", custom_prompt="", use_kb=True):
    """根据知识库素材 + 模板，生成书面文字材料（blocks 结构，供 doc_write 渲染）。

    优先大模型成文；模型不可用/返回不合规时回落离线骨架（用检索到的素材或模板空骨架），
    保证任何情况下都能出一份可编辑的稿子（空缺处标【待补充】）。
    """
    spec = _KB_DOC_TEMPLATES.get(template) or _KB_DOC_TEMPLATES["custom"]
    label = spec["label"]
    title = topic or label
    # ---- 1) 检索知识库素材（跨范围：项目库 + 公有库 + 分身私有库）----
    ctx, hits = "", 0
    if use_kb and (spec.get("query") or topic):
        try:
            from knowledge_base import multi_query, list_scopes
            names = [s.get("name") for s in list_scopes()]
            q = ((topic + " ") if topic else "") + (spec.get("query") or "")
            res = multi_query(q, names, top_k=12, alpha=0.6, threshold=0.0) if len(names) > 1 \
                else hybrid_query(q, names[0], top_k=12, threshold=0.0) if names else []
            hits = len(res)
            ctx = "\n\n".join("[素材%d · %s] %s" % (i + 1, r.get("label") or r.get("source") or "",
                                                 r.get("text", ""))
                              for i, r in enumerate(res))
        except Exception:
            ctx, hits = "", 0
    # ---- 2) 组装 prompt ----
    sections = custom_prompt if template == "custom" else spec["sections"]
    user = "文字材料类型：%s\n" % label
    if topic:
        user += "主题/对象：%s\n" % topic
    user += "语言：%s\n" % lang
    user += "章节结构：%s\n" % (sections or "请按通用汇报结构组织（概述 / 主要内容 / 总结）")
    if ctx:
        user += "\n知识库检索到的素材（仅供引用，无依据处标【待补充】）：\n%s\n" % ctx[:8000]
    else:
        user += "\n（未检索到知识库素材，请基于通用公文结构起草，空缺处标【待补充】。）\n"
    # ---- 3) 大模型成文 ----
    try:
        txt, _m = app_settings.chat(
            [{"role": "system", "content": _KB_DOC_SYSTEM},
             {"role": "user", "content": user}],
            cap="text", max_tokens=4000)
        data = _extract_json(txt)
        if isinstance(data, dict) and data.get("title"):
            title = data["title"]
        blocks = data.get("blocks") if isinstance(data, dict) else None
        blocks = [b for b in (blocks or [])
                  if isinstance(b, dict) and (b.get("text") or b.get("items"))]
        if len(blocks) >= 3:
            return {"title": title, "blocks": blocks, "source": "llm",
                    "kb_hits": hits, "template": label}
    except Exception:
        pass
    # ---- 4) 回落：离线骨架 ----
    blocks = _kb_doc_skeleton(title, sections or "一、概述；二、主要内容；三、总结", ctx)
    return {"title": title, "blocks": blocks, "source": "offline",
            "kb_hits": hits, "template": label}


def _kb_doc_skeleton(title, sections, ctx):
    blocks = [{"type": "h1", "text": title}]
    secs = [s.strip() for s in re.split(r"[；;、\n]", sections) if s.strip()]
    if not secs:
        secs = ["一、概述", "二、主要内容", "三、总结"]
    for s in secs:
        blocks.append({"type": "h2", "text": s})
        blocks.append({"type": "p",
                       "text": (ctx[:600] + " …（请据此补充展开；无依据处填【待补充】）") if ctx
                       else "【待补充】"})
    return blocks


def _handle_gen_kb_doc(self, data):
    template = data.get("template") or "summary"
    lang = data.get("lang") or "中文"
    topic = (data.get("topic") or "").strip()
    custom = (data.get("custom_prompt") or "").strip()
    try:
        doc = _gen_kb_doc(template, lang=lang, topic=topic, custom_prompt=custom, use_kb=True)
    except Exception as e:
        self._json({"ok": False, "error": str(e)}); return
    out = ""
    try:
        os.makedirs(config.DIR_TEST, exist_ok=True)
        fn = "kb_doc_%s_%s.docx" % (template, time.strftime("%Y%m%d_%H%M%S"))
        p = os.path.join(config.DIR_TEST, fn)
        doc_write.write_docx(doc.get("title") or "文字材料", doc["blocks"], p)
        out = "/api/download?name=" + fn
    except Exception:
        out = ""
    self._json({"ok": True, "title": doc.get("title"), "blocks": doc.get("blocks"),
                "source": doc.get("source"), "kb_hits": doc.get("kb_hits", 0),
                "template": doc.get("template"), "download": out})


# ============ 答疑记录（演示/讲解期间问答沉淀，供总结与入库）============
# 持久化到磁盘：演示多次、刷新页面都不丢；总结面板据此生成《答疑汇总》并可回灌知识库。
# 答疑记录文件由 config 按「当前激活项目」动态维护（切换项目即换库），此处不再重绑常量。


def _load_qa_log():
    try:
        if os.path.exists(config.QA_LOG_FILE):
            with open(config.QA_LOG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("records", [])
    except Exception:
        pass
    return []


def _save_qa_log(records):
    try:
        with open(config.QA_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump({"records": records}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return records


def _summarize_qa(recs):
    """把问答记录整理成《答疑汇总》Markdown（大模型的活；失败兜底返回原始记录）。"""
    if not recs:
        return "（本次演示暂无答疑记录）"
    lines = []
    for i, r in enumerate(recs, 1):
        lines.append("### Q%d（%s，%s）\n**问**：%s\n**答**：%s\n"
                     % (i, r.get("mode", ""), r.get("time", ""),
                        r.get("question", ""), r.get("answer", "")))
    joined = "\n".join(lines)
    prompt = ("你是严谨的演示文档整理助手。下面是一次演示/讲解过程中，听众（如领导）提出的问题与"
              "数字分身的回答记录。请据此整理一份《答疑汇总》文档，结构如下：\n"
              "1. 概述：本次演示共记录 N 条问答，主题集中在……\n"
              "2. 常见问题与解答（保留原问答要点，相似问题可合并归纳）\n"
              "3. 待跟进事项（若问题超出知识库、回答中提示需进一步确认的内容）\n"
              "4. 知识库补充建议（可沉淀进知识库的资料方向）\n"
              "使用中文，简洁专业，不要编造记录中未出现的内容。\n\n原始记录：\n" + joined)
    try:
        from qa_brain import ollama_chat
        return ollama_chat(
            [{"role": "system", "content": "你是严谨的文档整理助手，只基于给定材料归纳，不编造。"},
             {"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=1500)
    except Exception:
        return "# 答疑汇总（自动整理失败，返回原始记录）\n\n" + joined


# ============ 当前 PPT 页缓存（供前端同步展示 + 答疑关联定位）============
# 持久化到磁盘：否则服务重启后前端就看不到已解析的页面了。
# 幻灯片/版本存储文件由 config 按「当前激活项目」动态维护，此处不再重绑常量。
SLIDES = []          # [{idx,title,text,notes,images:[url],img,audio,narration,narration_en}]
SLIDES_META = {"source": "", "count": 0}
# ============ 多版本讲解 ============
# VERSIONS: {vid: {"name","created","slides":[{idx,title,narration,narration_en,audio,...}],"audio_full":url}}
# VERSION_ORDER: 版本 id 顺序；CURRENT_VERSION: 当前演示版本 id（SLIDES 即该版本的"展开态"）
VERSIONS = {}
VERSION_ORDER = []
CURRENT_VERSION = "default"


def _normalize_audio(v):
    """把历史脏数据（如 'narrations\\narr-01.wav' 这种带换行的相对路径）规范成
    /api/audio?name=xxx，保证正式演示每页都能取到音频。已是标准 URL 的原样返回。"""
    if not v:
        return v
    if str(v).startswith("/api/audio?"):
        return v
    base = os.path.basename(str(v).replace("\\", "/").replace("\n", "/").replace("\r", "/"))
    return ("/api/audio?name=" + base) if base else v


def _normalize_all_audio():
    """载入/保存前统一修一遍音频链接，防止脏路径导致演示中途失声或整页被跳过。"""
    for s in SLIDES or []:
        if s.get("audio"):
            s["audio"] = _normalize_audio(s["audio"])
        if s.get("audio_en"):
            s["audio_en"] = _normalize_audio(s["audio_en"])
    for v in (VERSIONS or {}).values():
        for s in v.get("slides", []) or []:
            if s.get("audio"):
                s["audio"] = _normalize_audio(s["audio"])
        if v.get("audio_full"):
            v["audio_full"] = _normalize_audio(v["audio_full"])
    if SLIDES_META.get("audio_full"):
        SLIDES_META["audio_full"] = _normalize_audio(SLIDES_META["audio_full"])


def _load_slides():
    global SLIDES, SLIDES_META, VERSIONS, VERSION_ORDER, CURRENT_VERSION
    # 先归零：切换项目（或新项目尚无 slides.json）时必须清空上一项目的讲稿/版本，
    # 否则会残留旧数据，表现为"切了项目但页面还是上一个项目的内容"。
    SLIDES = []
    SLIDES_META = {"source": "", "count": 0}
    VERSIONS = {}
    VERSION_ORDER = []
    CURRENT_VERSION = "default"
    try:
        if os.path.exists(config.SLIDES_FILE):
            with open(config.SLIDES_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            SLIDES = d.get("slides", [])
            SLIDES_META = d.get("meta", {"source": "", "count": len(SLIDES)})
            VERSIONS = d.get("versions") or {}
            vm = SLIDES_META.get("versions") or []
            VERSION_ORDER = [v.get("id") for v in vm] or list(VERSIONS.keys())
            # 防御：把字典里存在但漏登到 order 的版本补齐，避免保存时被清掉
            for k in VERSIONS:
                if k not in VERSION_ORDER:
                    VERSION_ORDER.append(k)
            CURRENT_VERSION = SLIDES_META.get("currentVersion") or (VERSION_ORDER[0] if VERSION_ORDER else "default")
            _normalize_all_audio()
            # 首次迁移：把当前内容固化为"默认讲解"版本，便于多版本来回切换
            if not VERSIONS:
                VERSIONS = {"default": {
                    "name": "默认讲解", "created": time.strftime("%Y-%m-%d %H:%M"),
                    "slides": [dict(s) for s in SLIDES],
                    "audio_full": SLIDES_META.get("audio_full")}}
                VERSION_ORDER = ["default"]
                CURRENT_VERSION = "default"
                SLIDES_META["versions"] = [{"id": "default", "name": "默认讲解",
                                           "created": VERSIONS["default"]["created"]}]
                SLIDES_META["currentVersion"] = "default"
                _save_slides()
    except Exception:
        pass


def _save_slides():
    try:
        with open(config.SLIDES_FILE, "w", encoding="utf-8") as f:
            json.dump({"slides": SLIDES, "meta": SLIDES_META, "versions": VERSIONS},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _apply_version(vid):
    """把指定版本讲稿载入 SLIDES（当前演示态），并持久化当前版本指针。

    版本存储的是「完整 slides」（含 title/text/img/notes/音频）。
    若版本携带完整页脚手架（含 img 或 text），则整页替换 SLIDES，
    以支撑不同页数的版本（如 default 20页 / 培训版 30页）。
    旧式仅存讲稿（无 img/text）的版本则按 idx 合并 narration/audio 到当前脚手架。"""
    global CURRENT_VERSION, SLIDES
    v = VERSIONS.get(vid)
    if not v:
        return
    vs_slides = v.get("slides", [])
    if vs_slides and any(("img" in s or "text" in s) for s in vs_slides):
        SLIDES = [dict(s) for s in vs_slides]
    else:
        vmap = {s["idx"]: s for s in vs_slides}
        for sl in SLIDES:
            vs = vmap.get(sl["idx"])
            if vs:
                sl["narration"] = vs.get("narration", "")
                sl["narration_en"] = vs.get("narration_en", "")
                sl["audio"] = vs.get("audio")
    CURRENT_VERSION = vid
    SLIDES_META["currentVersion"] = vid
    SLIDES_META["versionName"] = v.get("name")
    SLIDES_META["audio_full"] = v.get("audio_full")
    SLIDES_META["versions"] = [{"id": k, "name": VERSIONS[k]["name"],
                                "created": VERSIONS[k].get("created", "")} for k in VERSION_ORDER]
    _save_slides()


def _translate_text(text, target="English"):
    """本地 Ollama 中英互译（状态栏双语/中英对照用）。失败返回空串。"""
    if not text or not text.strip():
        return ""
    try:
        from qa_brain import ollama_chat
        sys_p = ("你是专业中英互译助手。只输出译文本身，不要解释、不要引号、不要任何额外文字；"
                 "保持原文的专业术语、数字、人名与段落结构不变。")
        if target == "English":
            usr = "把下面的中文翻译成英文（专业、自然、口语化）：\n" + text
        else:
            usr = "Translate the following into Chinese (natural, spoken style):\n" + text
        return ollama_chat(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
            temperature=0.3, max_tokens=len(text) * 2 + 300)
    except Exception as e:
        print("[translate] 失败:", e)
        return ""


def _job_worker_translate(jid):
    """后台：把当前版本全部页面讲解翻译为英文，写入 narration_en 并持久化。"""
    try:
        total = len(SLIDES)
        _job_update(jid, stage="翻译为英文…", total=total, page=0)
        for i, sl in enumerate(SLIDES, 1):
            _job_update(jid, stage="第 %d/%d 页：翻译" % (i, total), page=i)
            en = sl.get("narration_en") or _translate_text(sl.get("narration", "") or "", "English")
            sl["narration_en"] = en
            v = VERSIONS.get(CURRENT_VERSION)
            if v:
                for vs in v["slides"]:
                    if vs.get("idx") == sl.get("idx"):
                        vs["narration_en"] = en
                        break
            _save_slides()
        _job_update(jid, stage="完成", done=True, page=total,
                    summary="已将 %d 页讲解翻译为英文，状态栏可切换「英文 / 中英对照」显示。" % total)
    except Exception as e:
        _job_update(jid, done=True, error=str(e), stage="失败")


def _job_worker_version(jid, name, lang, words):
    """后台：基于当前 PPT 原稿重新生成一套讲稿+炎冰语音，存为新版本并设为当前。"""
    try:
        src_path = SLIDES_META.get("source_path")
        if src_path and os.path.exists(src_path):
            base = pptx_parse.parse(src_path)
        else:
            base = [{"idx": s["idx"], "title": s.get("title", ""), "text": s.get("text", ""),
                     "notes": s.get("notes", ""), "images": s.get("images", [])} for s in SLIDES]
        base.sort(key=lambda s: s["idx"])
        total = len(base)
        for sl in base:
            sl["_total"] = total
        vid = "v%d" % int(time.time())
        vslides, wav_paths = [], []
        for i, sl in enumerate(base, 1):
            _job_update(jid, stage="第 %d/%d 页：写讲稿" % (i, total), page=i - 1, total=total)
            from qa_brain import generate_narration
            text = generate_narration(sl, lang=lang, words=words, model="qwen3.5:9b")
            out = os.path.join(config.DIR_NARR, "narr_%s_%02d.wav" % (vid, sl["idx"]))
            ok = narrate.synth_one(text, lang, out, seed=TTS_SEED_BASE + i, ref=resolve_voice())
            item = {"idx": sl["idx"], "title": sl.get("title", ""),
                    "narration": text, "narration_en": "",
                    "audio": ("/api/audio?name=" + os.path.basename(out)) if ok else None}
            vslides.append(item)
            if ok:
                wav_paths.append(out)
            with JOBS_LOCK:
                j = JOBS.get(jid)
                if j:
                    j["narrations"] = list(vslides)
                    j["page"] = i
        _job_update(jid, stage="合并全篇音频…")
        full = narrate.concat_audio(wav_paths, os.path.join(config.DIR_NARR, "full_%s.wav" % vid))
        VERSIONS[vid] = {
            "name": name or ("讲解版 %d" % (len(VERSION_ORDER) + 1)),
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "slides": vslides,
            "audio_full": ("/api/audio?name=" + os.path.basename(full)) if full else None}
        if vid not in VERSION_ORDER:
            VERSION_ORDER.append(vid)
        _apply_version(vid)
        _job_update(jid, stage="完成", done=True, page=total,
                    summary="已生成新讲解版本「%s」，共 %d 页，已设为当前演示版本。" % (VERSIONS[vid]["name"], total),
                    full_audio=("/api/audio?name=" + os.path.basename(full)) if full else None)
    except Exception as e:
        _job_update(jid, done=True, error=str(e), stage="失败")


# 项目系统必须先初始化：迁移顶层遗留数据到 default 项目，并把各数据目录/文件重算到「当前激活项目」，
# 之后 _load_slides() 才能读到本项目的 slides.json（否则会读到空讲稿或残留上一项目数据）。
config.init_projects()
_load_slides()


def _sync_active_person():
    """启动期自愈：把 settings.json 里「用户上次选的分身」同步进 persons.json。

    旧版本里前端只写 settings.json、后端只读 persons.json.default，两边长期不一致，
    表现为「界面选了金子，语音合成却还是炎冰」。这里在每次启动时对齐一次，
    并对超出象库范围的旧值做一次清理，避免带着错误状态继续跑。
    """
    try:
        st = load_settings() or {}
        sp = (st.get("person") or "").strip()
        d = persons.load()
        ids = [p.get("id") for p in d.get("persons", [])]
        if sp and sp in ids and d.get("default") != sp:
            persons.set_active(sp, (st.get("avatar") or "").strip())
            print("[scope] 已同步当前分身：%s" % sp, flush=True)
        elif sp and sp not in ids:
            print("[scope] settings.person=%r 不在象库中，忽略" % sp, flush=True)
    except Exception as e:
        print("[scope] 同步当前分身失败：%s" % e, flush=True)


_sync_active_person()


def _slides_to_urls(slides):
    """把本地图片绝对路径换成 /api/img?name= 可访问 URL。"""
    out = []
    for s in slides:
        out.append({
            "idx": s.get("idx"), "title": s.get("title", ""),
            "text": s.get("text", ""), "notes": s.get("notes", ""),
            "images": ["/api/img?name=" + os.path.basename(x) for x in s.get("images", [])],
        })
    return out


def _render_slide_images(src_path, jid=None):
    """把 PPT 渲染成原稿版式页图并挂到 SLIDES[*].img（soffice→PDF→MuPNG）。

    渲染失败不致命：前端回退到 HTML 版式展示。须在后台线程调用（soffice 约 10-20s）。
    """
    try:
        upd = (lambda **k: _job_update(jid, **k)) if jid else (lambda **k: None)
        upd(stage="渲染 PPT 原稿版式页图…")
        import slide_render
        img_dir, paths = slide_render.render_deck(
            src_path, progress=lambda c, t: upd(page=c, total=t))
        for i, p in enumerate(paths, 1):
            fn = os.path.basename(p)
            dst = os.path.join(config.DIR_TEST, fn)
            shutil.copy(p, dst)
            for sl in SLIDES:
                if sl.get("idx") == i:
                    sl["img"] = "/api/img?name=" + fn
        _save_slides()
        upd(stage="页图渲染完成")
        return True
    except Exception as e:
        print("[slide_render] 渲染失败（回退 HTML 版式）:", e)
        return False


def _send(handler, code, body, ctype="application/json; charset=utf-8", extra_headers=None):
    if isinstance(body, str):
        body = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    if extra_headers:
        for k, v in extra_headers.items():
            handler.send_header(k, v)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        _send(self, code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/", "/index.html"):
            cur = get_current_persona()
            _send(self, 200, INDEX_HTML
                  .replace("__APP__", APP_NAME)
                  .replace("__NAME__", cur["name"])
                  .replace("__TITLE__", cur["title"]),
                  "text/html; charset=utf-8")
        elif p.path == "/manifest.json":
            _send(self, 200, MANIFEST, "application/manifest+json; charset=utf-8")
        elif p.path == "/sw.js":
            _send(self, 200, SW_JS, "application/javascript; charset=utf-8")
        elif p.path in ("/icon-192.png", "/icon-512.png"):
            fp = os.path.join(HERE, os.path.basename(p.path))
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    _send(self, 200, f.read(), "image/png")
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/import_progress":
            # zip 导入进度（上传 + 解压两阶段），job 由前端生成并传给 /api/upload_zip
            jid = urllib.parse.parse_qs(p.query).get("job", [""])[0]
            self._json({"ok": True, **_imp_get(jid)})
        elif p.path == "/api/img":
            # 幻灯片内嵌图片（kb_data/imgs_* 下）+ PPT 原稿版式页图（config.DIR_TEST 下）
            name = urllib.parse.parse_qs(p.query).get("name", [""])[0]
            name = os.path.basename(name)
            fp = None
            for d in (config.DIR_TEST, config.DIR_KB):
                for root, _dirs, files in os.walk(d):
                    if name in files:
                        fp = os.path.join(root, name); break
                if fp: break
            if fp:
                ext = os.path.splitext(fp)[1].lower()
                ct = {".png": "image/png", ".jpg": "image/jpeg",
                      ".jpeg": "image/jpeg", ".gif": "image/gif",
                      ".bmp": "image/bmp", ".webp": "image/webp"}.get(ext, "image/png")
                with open(fp, "rb") as f:
                    _send(self, 200, f.read(), ct)
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/portrait":
            # 分身形象图（avatar_out/portraits 下）
            fn = os.path.basename(urllib.parse.parse_qs(p.query).get("file", [""])[0])
            fp = os.path.join(config.PORTRAITS_DIR, fn)
            if fn and os.path.exists(fp):
                ext = os.path.splitext(fp)[1].lower()
                ct = {".png": "image/png", ".jpg": "image/jpeg",
                      ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/jpeg")
                with open(fp, "rb") as f:
                    _send(self, 200, f.read(), ct)
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/audio":
            name = os.path.basename(urllib.parse.parse_qs(p.query).get("name", [""])[0])
            fp = None
            for d in (config.DIR_NARR, config.DIR_TEST):
                for root, _dirs, files in os.walk(d):
                    if name in files:
                        fp = os.path.join(root, name)
                        break
                if fp:
                    break
            # 兼容「压缩导出」：包内音频可能是转码后的 .mp3，而 slides.json 仍写 .wav
            # （导出时已做引用改写，这里再兜一层，保证旧包/混合包也能播）
            if not fp and name.lower().endswith(".wav"):
                alt = name[:-4] + ".mp3"
                for d in (config.DIR_NARR, config.DIR_TEST):
                    for root, _dirs, files in os.walk(d):
                        if alt in files:
                            fp = os.path.join(root, alt); name = alt
                            break
                    if fp:
                        break
            if not fp and name.lower().endswith(".mp3"):
                alt = name[:-4] + ".wav"
                for d in (config.DIR_NARR, config.DIR_TEST):
                    for root, _dirs, files in os.walk(d):
                        if alt in files:
                            fp = os.path.join(root, alt); name = alt
                            break
                    if fp:
                        break
            if fp:
                ctype = "audio/mpeg" if name.lower().endswith(".mp3") else "audio/wav"
                # 流式返回：40MB 的 wav 不再整个读进内存
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(os.path.getsize(fp)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    with open(fp, "rb") as f:
                        while True:
                            buf = f.read(1024 * 1024)
                            if not buf:
                                break
                            self.wfile.write(buf)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/avatars":
            self._json(list_portraits())
        elif p.path == "/api/projects":
            self._json({"ok": True, "active": config.get_active_project(),
                        "projects": config.list_projects()})
        elif p.path == "/api/versions":
            self._handle_versions({"act": "list"})
        elif p.path == "/api/slides":
            self._json({"slides": SLIDES, "meta": SLIDES_META})
        elif p.path == "/api/settings":
            self._json({"ok": True, "settings": load_settings()})
        elif p.path == "/api/llm_config":
            self._json({"ok": True, "config": llm_client.load_llm_config()})
        # ---- v2.6 统一设置：多模型配置 / 主备 / ComfyUI / 默认文件夹 / 依赖自检 ----
        elif p.path == "/api/app_settings":
            s = app_settings.load()
            s["folders_effective"] = app_settings.folders()
            s["capabilities"] = app_settings.CAPABILITIES
            self._json({"ok": True, "settings": s})
        elif p.path == "/api/models":
            self._json({"ok": True, "models": app_settings.list_models(),
                        "assign": app_settings.load().get("assign") or {},
                        "capabilities": app_settings.CAPABILITIES})
        elif p.path == "/api/deps":
            self._json({"ok": True, "deps": app_settings.deps_status()})
        elif p.path == "/api/runtime":
            self._json({"ok": True, **app_settings.runtime_status(),
                        "meta": app_settings.RUNTIME_KEYS})
        elif p.path == "/api/templates":
            self._json({"ok": True, **ppt_templates.list_templates()})
        # ---- v2.6 公有 / 私有知识库 ----
        elif p.path == "/api/kb/scopes":
            self._json({"ok": True, "scopes": knowledge_base.list_scopes()})
        elif p.path == "/api/folders":
            self._json({"ok": True, "folders": app_settings.folders(),
                        "configured": app_settings.load().get("folders") or {}})
        elif p.path == "/api/persons":
            self._json(persons.list_persons())
        elif p.path == "/api/scope":
            act = persons.active()
            self._json({"ok": True, "active": act,
                        "voice": os.path.basename(resolve_voice(act["person"])),
                        "voice_ok": os.path.exists(resolve_voice(act["person"])),
                        "avatar_file": os.path.basename(
                            persons.resolve(act["person"], act["avatar"]) or "")})
        elif p.path == "/api/qa_log":
            self._json({"ok": True, "records": _load_qa_log()})
        elif p.path == "/api/video":
            name = urllib.parse.parse_qs(p.query).get("name", [""])[0]
            fp = None
            for d in (config.DIR_AVATAR, config.DIR_TEST):
                cand = os.path.join(d, os.path.basename(name))
                if os.path.exists(cand):
                    fp = cand
                    break
            if fp:
                with open(fp, "rb") as f:
                    _send(self, 200, f.read(), "video/mp4")
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/demo_avatar_status":
            version = urllib.parse.parse_qs(p.query).get("version", [CURRENT_VERSION])[0]
            self._json({"ok": True, "version": version, "videos": _demo_av_status(version)})
        elif p.path == "/api/demo_avatar_full":
            version = urllib.parse.parse_qs(p.query).get("version", [CURRENT_VERSION])[0]
            out_mp4 = _demo_av_full_path(version)
            dur_file = _demo_av_full_dur(version)
            if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0 and os.path.exists(dur_file):
                with open(dur_file, "r", encoding="utf-8") as f:
                    dd = json.load(f)
                self._json({"ok": True, "version": version, "ready": True,
                            "video": "/api/video?name=" + os.path.basename(out_mp4),
                            "dur": dd.get("timeline"), "total": dd.get("total")})
            else:
                self._json({"ok": True, "version": version, "ready": False})
        elif p.path == "/api/demo_avatar_clips":
            # 内置分身演示片段清单（2026-10-03 起）：炎冰/金子 Sonic 出片默认打进 APP，
            # 供「验证环境」演示模态直接播放，无需用户先跑一次推理。
            clips = []
            d = os.path.join(HERE, "demo_avatar")
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if fn.endswith(".mp4") and os.path.getsize(os.path.join(d, fn)) > 0:
                        clips.append(fn)
            self._json({"ok": True, "clips": clips})
        elif p.path == "/api/demo_avatar_clip":
            name = os.path.basename(urllib.parse.parse_qs(p.query).get("file", [""])[0])
            fp = os.path.join(HERE, "demo_avatar", name)
            if name.endswith(".mp4") and os.path.exists(fp) and os.path.getsize(fp) > 0:
                with open(fp, "rb") as f:
                    _send(self, 200, f.read(), "video/mp4")
            else:
                _send(self, 404, "not found")
        elif p.path == "/api/meta":
            cur = get_current_persona()
            act = persons.active()
            self._json({"ok": True, "playback": PLAYBACK_MODE,
                        "app_name": APP_NAME,
                        "name": cur["name"], "title": cur["title"],
                        "person": act.get("person", ""), "avatar": act.get("avatar", ""),
                        "version": getattr(sys.modules.get("config"), "VERSION", "") or "2.3.0"})
        elif p.path == "/api/playback_files":
            # 列出可作为"播放文件"导出的整场分身视频（供前端勾选）
            vids = []
            if os.path.isdir(config.DIR_AVATAR):
                for fn in sorted(os.listdir(config.DIR_AVATAR)):
                    if fn.startswith("demo_av_full_") and fn.endswith(".mp4") \
                            and os.path.getsize(os.path.join(config.DIR_AVATAR, fn)) > 0:
                        vids.append(fn)
            self._json({"ok": True, "videos": vids})
        elif p.path == "/api/download":
            name = os.path.basename(urllib.parse.parse_qs(p.query).get("name", [""])[0])
            fp = None
            for d in (config.DIR_TEST, DATA_DIR, config.DIR_NARR, config.DIR_AVATAR,
                      app_settings.folders().get("export", "")):
                cand = os.path.join(d, name)
                if os.path.exists(cand) and os.path.isfile(cand):
                    fp = cand; break
            if fp:
                ext = os.path.splitext(fp)[1].lower()
                ctype = {
                    ".zip": "application/zip",
                    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    ".ppt": "application/vnd.ms-powerpoint",
                    ".pdf": "application/pdf",
                    ".md": "text/markdown; charset=utf-8",
                }.get(ext, "application/octet-stream")
                ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', name)
                disp = 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (
                    ascii_name, urllib.parse.quote(name))
                _send(self, 200, open(fp, "rb").read(), ctype,
                      {"Content-Disposition": disp})
            else:
                _send(self, 404, "not found")
        else:
            _send(self, 404, "not found")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        # 播放版：屏蔽一切"创作类"写接口（仅放行 upload_zip 导入播放包 / resources 导入 /
        # 进度 / 读取 / 设置）。必须在 upload_zip / kb_upload 二进制直传分流之前判断，
        # 否则 kb_upload 等接口会绕过本限制，导致"播放版仅演示"被突破。
        if PLAYBACK_MODE:
            _PLAYBACK_FORBIDDEN = {
                "/api/translate", "/api/translate_all", "/api/versions",
                "/api/process", "/api/persons", "/api/avatar", "/api/scope",
                "/api/demo_avatar_video", "/api/demo_avatar_full",
                "/api/ingest", "/api/sources", "/api/backup_import",
                "/api/ask", "/api/asr",
                "/api/kb_backup", "/api/kb_rebuild", "/api/kb_upload",
                "/api/make_ppt",
                # v2.6 新增的创作侧写接口：模型配置增删改 / ComfyUI 配置 / 文件夹设置
                "/api/model/save", "/api/model/delete", "/api/model/test",
                "/api/model/assign",
                "/api/comfyui/config", "/api/comfyui/test", "/api/comfyui/scan",
                "/api/folders/save", "/api/browse",
                "/api/runtime/save", "/api/runtime/scan",
                "/api/templates/add", "/api/templates/upload", "/api/templates/delete",
            }
            if p.path in _PLAYBACK_FORBIDDEN:
                self._json({"ok": False,
                            "error": "播放版仅支持演示播放；创作功能请使用完整版。"}); return
        # 大文件 zip 直传（二进制流）：必须在读 JSON body 之前分流，
        # 避免前端把几百 MB 的 zip 转 base64 塞 JSON 撑爆 WebView2（Out of Memory）。
        # 播放版仍需允许 /api/upload_zip 导入播放包，故不放入 _PLAYBACK_FORBIDDEN。
        if p.path == "/api/upload_zip":
            _handle_upload_zip(self); return
        if p.path == "/api/kb_upload":
            _handle_kb_upload(self); return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json({"error": "bad json"}); return
        if p.path == "/api/projects":
            self._handle_projects(data); return
        if p.path == "/api/avatars":
            # 前端用 POST 拉取分身列表（与 do_GET 同逻辑，避免 404）
            self._json(list_portraits()); return
        if p.path == "/api/translate":
            self._handle_translate(data)
        elif p.path == "/api/translate_all":
            jid = _job_new(total=len(SLIDES))
            t = threading.Thread(target=_job_worker_translate, args=(jid,), daemon=True)
            t.start()
            self._json({"job": jid, "stage": "已提交，开始翻译…"}); return
        elif p.path == "/api/versions":
            self._handle_versions(data)
        elif p.path == "/api/process":
            self._handle_process(data)
        elif p.path == "/api/progress":
            self._handle_progress(data)
        elif p.path == "/api/slides":
            self._json({"slides": SLIDES, "meta": SLIDES_META})
        elif p.path == "/api/persons":
            self._handle_persons(data)
        elif p.path == "/api/settings":
            self._handle_settings(data)
        elif p.path == "/api/scope":
            self._handle_scope(data)
        elif p.path == "/api/llm_config":
            self._handle_llm_config(data)
        # ---- v2.6 统一设置：模型记录的增改删查 / 连通性测试 / 主备指派 ----
        elif p.path == "/api/model/save":
            rec = app_settings.upsert_model(data)
            self._json({"ok": True, "model": rec, "models": app_settings.list_models()})
        elif p.path == "/api/model/delete":
            app_settings.delete_model(data.get("id", ""))
            self._json({"ok": True, "models": app_settings.list_models()})
        elif p.path == "/api/model/test":
            mid = data.get("id")
            rec = (app_settings.get_model(mid) if mid else None) or data
            r = app_settings.test_model(rec)
            r["ok"] = r["ok"] or False
            self._json(r)
        elif p.path == "/api/model/assign":
            cap = data.get("cap") or "text"
            self._json({"ok": True, "cap": cap,
                        "assign": app_settings.set_assign(cap, data.get("primary", ""),
                                                          data.get("backup", ""))})
        # ---- v2.6 ComfyUI 目录与启动文件 ----
        elif p.path == "/api/comfyui/config":
            cfg = app_settings.save_comfyui(data)
            self._json({"ok": True, "config": cfg})
        elif p.path == "/api/comfyui/test":
            self._json(app_settings.test_comfyui(data.get("api_url")))
        elif p.path == "/api/comfyui/scan":
            root = data.get("dir") or app_settings.find_comfyui_root()
            self._json({"ok": bool(root), "root": root,
                        "launchers": app_settings.list_comfyui_launchers(root)})
        # ---- v2.6 运行时目录（绿色伴随目录：LivePortrait / ts_deps / ComfyUI / ffmpeg）----
        elif p.path == "/api/runtime/save":
            self._json({"ok": True, **app_settings.save_runtime(data)})
        elif p.path == "/api/runtime/scan":
            f = app_settings.scan_runtime()
            self._json({"ok": True, "found": f, **app_settings.save_runtime(f)})
        # ---- v2.6 默认文件夹 ----
        elif p.path == "/api/folders/save":
            f = app_settings.save_folders(data)
            app_settings.ensure_folders()
            self._json({"ok": True, "folders": f})
        elif p.path == "/api/browse":
            self._handle_browse(data)
        # ---- v2.6 PPT 模板：服务端路径登记 / 小文件直传 / 删除 ----
        elif p.path == "/api/templates/add":
            src = (data.get("path") or "").strip()
            if not src:
                self._json({"ok": False, "error": "请填写模板文件的完整路径"}); return
            ok, tid, msg = ppt_templates.add_template(src, data.get("name"))
            self._json({"ok": ok, "id": tid, "msg": msg,
                        "templates": ppt_templates.list_templates()["templates"]})
        elif p.path == "/api/templates/upload":
            try:
                raw = base64.b64decode(data.get("data", ""))
                if len(raw) > 80 * 1024 * 1024:
                    self._json({"ok": False, "error": "模板超过 80MB，请改用「从本机路径登记」方式添加"}); return
                tmp = os.path.join(config.DIR_TEST, "tpl_upload" + (data.get("ext") or ".pptx"))
                os.makedirs(config.DIR_TEST, exist_ok=True)
                with open(tmp, "wb") as f:
                    f.write(raw)
            except Exception as e:
                self._json({"ok": False, "error": "上传失败：%s" % e}); return
            ok, tid, msg = ppt_templates.add_template(tmp, data.get("name"))
            self._json({"ok": ok, "id": tid, "msg": msg,
                        "templates": ppt_templates.list_templates()["templates"]})
        elif p.path == "/api/templates/delete":
            ppt_templates.delete_template(data.get("id", ""))
            self._json({"ok": True, "templates": ppt_templates.list_templates()["templates"]})
        # ---- v2.6 私有知识提升进公有（沉淀复用，不删源）----
        elif p.path == "/api/kb/promote":
            try:
                r = knowledge_base.promote_to_public(
                    data.get("src") or KB_NAME,
                    limit=(int(data.get("limit")) if data.get("limit") else None),
                    category=data.get("category") or "由私有提升")
                self._json({"ok": True, **r, "scopes": knowledge_base.list_scopes()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        elif p.path == "/api/ask":
            self._handle_ask(data)
        elif p.path == "/api/qa_log":
            self._handle_qa_log(data)
        elif p.path == "/api/asr":
            self._handle_asr(data)
        elif p.path == "/api/ingest":
            self._handle_ingest(data)
        elif p.path == "/api/sources":
            self._handle_sources(data)
        elif p.path == "/api/avatar":
            self._handle_avatar(data)
        elif p.path == "/api/demo_avatar_video":
            _handle_demo_avatar_video(self, data)
        elif p.path == "/api/demo_avatar_full":
            _handle_demo_avatar_full(self, data)
        elif p.path == "/api/resources":
            _handle_resources(self, data)
        elif p.path == "/api/backup_list":
            self._handle_backup_list(data)
        elif p.path == "/api/backup_import":
            self._handle_backup_import(data)
        elif p.path == "/api/make_ppt":
            self._handle_make_ppt(data)
        elif p.path == "/api/gen_kb_doc":
            self._handle_gen_kb_doc(data)
        elif p.path == "/api/kb_backup":
            self._handle_kb_backup(data)
        elif p.path == "/api/kb_rebuild":
            self._handle_kb_rebuild(data)
        else:
            self._json({"error": "unknown endpoint"})

    # ---------- 备用素材目录（D:/Users/ppt，离线兜底通道）----------
    def _handle_backup_list(self, data=None):
        """列出备用目录下的演示文稿与文档，供前端直接挑选（不走上传通道）。"""
        try:
            base = os.path.abspath(DIR_BACKUP)
            if not os.path.isdir(base):
                self._json({"ok": False, "error": "备用目录不存在：%s" % base,
                            "dir": base, "decks": [], "docs": []}); return
            decks, docs = [], []
            for fn in sorted(os.listdir(base)):
                fp = os.path.join(base, fn)
                if not os.path.isfile(fp) or fn.startswith("~$"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                sz = os.path.getsize(fp)
                item = {"name": fn, "size": sz, "size_mb": round(sz / 1048576.0, 1),
                        "mtime": time.strftime("%Y-%m-%d %H:%M",
                                               time.localtime(os.path.getmtime(fp)))}
                if ext in BACKUP_DECK_EXT:
                    decks.append(item)
                elif ext in BACKUP_DOC_EXT:
                    docs.append(item)
            self._json({"ok": True, "dir": base, "decks": decks, "docs": docs})
        except Exception as e:
            self._json({"ok": False, "error": str(e), "dir": DIR_BACKUP,
                        "decks": [], "docs": []})

    def _handle_backup_import(self, data):
        """从备用目录取 PPT（必选）+ 讲解文本（可选）生成讲解；act=kb 时仅并入知识库。"""
        try:
            base = os.path.abspath(DIR_BACKUP)
            if not os.path.isdir(base):
                self._json({"error": "备用目录不存在：%s" % base}); return

            def safe(name):
                fp = os.path.abspath(os.path.join(base, name))
                if not fp.startswith(base + os.sep) and fp != base:
                    raise ValueError("文件不在备用目录内：%s" % name)
                if not os.path.isfile(fp):
                    raise ValueError("文件不存在：%s" % name)
                return fp

            act = (data.get("act") or "deck").strip()
            narr = (data.get("narr") or "").strip()
            # —— 仅把文档并入知识库（自我学习通道）
            if act == "kb":
                if not narr:
                    self._json({"error": "请先选择一个文档"}); return
                fp = safe(narr)
                ext = os.path.splitext(fp)[1].lower()
                if ext in (".pdf", ".doc", ".xls", ".rtf"):
                    d = doc_parse.extract(fp)
                    tp = os.path.join(config.DIR_TEST, "ingest.txt")
                    with open(tp, "w", encoding="utf-8") as f:
                        f.write(d["text"])
                    r = knowledge_base.ingest_document(tp, KB_NAME,
                                                      category=data.get("category", ""))
                    r["chars"] = len(d["text"])
                else:
                    r = knowledge_base.ingest_document(fp, KB_NAME,
                                                      category=data.get("category", ""))
                r["name"] = os.path.basename(fp)
                self._json(r); return
            # —— 生成讲解（PPT 必选，讲解文本可选）
            deck = (data.get("deck") or "").strip()
            if not deck:
                self._json({"error": "演示文稿（PPT）为必选项，请先在备用目录里选一个"}); return
            src = safe(deck)
            lang = data.get("lang", "中文")
            words = max(40, min(300, int(data.get("words", 120) or 120)))
            tmp = os.path.join(config.DIR_TEST, "upload.pptx")
            shutil.copy2(src, tmp)
            narr_map, narr_stats = {}, {"mode": "none"}
            if narr:
                import deck_narration
                narr_map, narr_stats = deck_narration.parse_narration_text(
                    deck_narration.read_narration_file(safe(narr)))
            jid = _job_new()
            _job_update(jid, narr_stats=narr_stats, narr_pages=len(narr_map))
            # 版本隔离：默认为本次导入新建独立版本（不覆盖当前演示态）；
            # 前端显式传 version 且已存在时更新该版本。
            ver_in = (data.get("version") or "").strip()
            if ver_in and ver_in in VERSIONS:
                ver_id, ver_name = ver_in, VERSIONS[ver_in].get("name") or ver_in
            else:
                ver_id = "backup_%s_%s" % (abs(hash(src)) % 10000,
                                           time.strftime("%m%d_%H%M"))
                ver_name = "备用讲解 · %s" % os.path.basename(src)
            t = threading.Thread(target=_job_worker, args=(jid, tmp, lang, words),
                                 kwargs={"narr_map": narr_map, "narr_stats": narr_stats,
                                         "version_id": ver_id, "version_name": ver_name},
                                 daemon=True)
            t.start()
            self._json({"job": jid, "stage": "已提交，开始处理…",
                        "source": os.path.basename(src),
                        "version": ver_id,
                        "narr": os.path.basename(narr) if narr else None})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_process(self, data):
        """异步化：立即返回 job_id，后台线程执行，前端轮询 /api/progress。"""
        try:
            b64 = data.get("data", "")
            lang = data.get("lang", "中文")
            # 讲解长度档位：控制每页讲稿字数（TTS 约 0.43 秒/字，这是总耗时的主杠杆）
            words = int(data.get("words", 120) or 120)
            words = max(40, min(300, words))
            tmp = os.path.join(config.DIR_TEST, "upload.pptx")
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(b64))
            # 讲解文本（可选）：有则按讲稿讲，某页没覆盖到仍自动补齐
            narr_raw = (data.get("narr") or "").strip()
            narr_map, narr_stats = {}, {"mode": "none"}
            if narr_raw:
                import deck_narration
                narr_map, narr_stats = deck_narration.parse_narration_text(narr_raw)
            jid = _job_new()
            # ⚠ 锁定「本次生成用的分身」：显式传参优先，否则用当前激活分身。
            #    以前只在文案上取当前分身、合成却用 resolve_voice(None)，导致
            #    「界面选金子、进度条写着金子、声音还是炎冰」。现在一次锁定、全程一致。
            _pid = (data.get("person") or "").strip() or None
            if _pid:
                try:
                    persons.set_active(_pid, (data.get("avatar") or "").strip() or None)
                except Exception:
                    pass
            else:
                _pid = persons.active().get("person") or None
            _job_update(jid, narr_stats=narr_stats, narr_pages=len(narr_map),
                        person=_pid or "", voice=os.path.basename(resolve_voice(_pid)))
            t = threading.Thread(target=_job_worker, args=(jid, tmp, lang, words),
                                 kwargs={"narr_map": narr_map,
                                         "narr_stats": narr_stats,
                                         "person": _pid},
                                 daemon=True)
            t.start()
            self._json({"job": jid, "stage": "已提交，开始处理…",
                        "narr_pages": len(narr_map),
                        "narr_mode": narr_stats.get("mode"),
                        "person": _pid or "",
                        "person_name": _cur_person_label(),
                        "voice": os.path.basename(resolve_voice(_pid))})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_progress(self, data):
        jid = (data.get("job") or "").strip()
        j = _job_get(jid)
        if not j:
            self._json({"error": "任务不存在或已过期"}); return
        pct = 0
        if j.get("total"):
            pct = int(min(100, (j.get("page") or 0) * 100 / j["total"]))
        self._json({
            "job": jid, "stage": j.get("stage"), "page": j.get("page"),
            "total": j.get("total"), "pct": pct, "done": j.get("done"),
            "error": j.get("error"), "summary": j.get("summary"),
            "narrations": j.get("narrations") or [],
            "full_audio": j.get("full_audio"),
            "elapsed": int(time.time() - j.get("started", time.time())),
        })

    def _handle_translate(self, data):
        try:
            text = (data.get("text") or "").strip()
            target = data.get("target", "English")
            if not text:
                self._json({"error": "空文本"}); return
            tr = _translate_text(text, target)
            self._json({"text": text, "translated": tr, "target": target})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_versions(self, data):
        try:
            act = (data.get("act") or "list").strip()
            if act == "list":
                items = [{"id": k, "name": VERSIONS[k]["name"],
                          "created": VERSIONS[k].get("created", ""),
                          "current": (k == CURRENT_VERSION),
                          "count": len(VERSIONS[k].get("slides", []))}
                         for k in VERSION_ORDER if k in VERSIONS]
                self._json({"versions": items, "current": CURRENT_VERSION}); return
            if act == "create":
                name = data.get("name") or ""
                lang = data.get("lang", "中文")
                words = int(data.get("words", 120) or 120); words = max(40, min(300, words))
                if not SLIDES:
                    self._json({"error": "请先在「① 准备工作」导入并生成讲解"}); return
                jid = _job_new(total=len(SLIDES))
                t = threading.Thread(target=_job_worker_version,
                                    args=(jid, name, lang, words), daemon=True)
                t.start()
                self._json({"job": jid, "stage": "已提交，开始生成新讲解版本…"}); return
            if act == "switch":
                vid = data.get("id")
                if vid and vid in VERSIONS:
                    _apply_version(vid)
                    self._json({"ok": True, "current": CURRENT_VERSION,
                                "slides": SLIDES, "meta": SLIDES_META}); return
                self._json({"error": "版本不存在"}); return
            if act == "rename":
                vid = data.get("id"); nm = (data.get("name") or "").strip()
                if vid in VERSIONS and nm:
                    VERSIONS[vid]["name"] = nm
                    SLIDES_META["versions"] = [{"id": k, "name": VERSIONS[k]["name"],
                                               "created": VERSIONS[k].get("created", "")}
                                              for k in VERSION_ORDER]
                    _save_slides(); self._json({"ok": True}); return
                self._json({"error": "参数错误"}); return
            if act == "set_en":
                idx = data.get("idx"); en = data.get("en", "")
                for sl in SLIDES:
                    if sl.get("idx") == idx:
                        sl["narration_en"] = en; break
                v = VERSIONS.get(CURRENT_VERSION)
                if v:
                    for vs in v["slides"]:
                        if vs.get("idx") == idx:
                            vs["narration_en"] = en; break
                _save_slides(); self._json({"ok": True}); return
            if act == "delete":
                vid = data.get("id")
                if vid in VERSIONS and len(VERSION_ORDER) > 1:
                    VERSION_ORDER.remove(vid); del VERSIONS[vid]
                    if CURRENT_VERSION == vid:
                        _apply_version(VERSION_ORDER[0])
                    SLIDES_META["versions"] = [{"id": k, "name": VERSIONS[k]["name"],
                                               "created": VERSIONS[k].get("created", "")}
                                              for k in VERSION_ORDER]
                    _save_slides()
                    self._json({"ok": True, "current": CURRENT_VERSION,
                                "slides": SLIDES, "meta": SLIDES_META}); return
                self._json({"error": "至少保留一个版本"}); return
            self._json({"error": "未知操作: " + act})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_ask(self, data):
        q = (data.get("question", "") or "").strip()
        lang = data.get("lang", "中文")                 # 回答语言（用户可选）
        q_lang = data.get("qlang") or _detect_lang(q)   # 提问语言（自动识别）
        if not q:
            self._json({"error": "空问题"}); return
        try:
            # v2.6 跨范围混合检索：项目库 + 公有库 + 当前分身私有库。
            # 私有结果加权（同问下分身个性优先于公有常识），返回的 scope 字段供前端标注来源。
            scopes = [KB_NAME, "public"]
            _cp = (data.get("person") or "").strip()
            if not _cp:
                try:
                    _cp = (load_settings() or {}).get("person") or ""
                except Exception:
                    _cp = ""
            if _cp:
                scopes.append("person:" + _cp)
            ctx_res = knowledge_base.multi_query(
                q, scopes, top_k=max(RETRIEVE_TOP_K, int(RETRIEVE_TOP_K * 1.5)),
                alpha=RETRIEVE_ALPHA, threshold=RETRIEVE_THRESHOLD)
            # 知识库范围：若前端勾选了部分来源，则只保留这些来源的检索结果
            picked = data.get("sources") or []
            if picked:
                picked = set(str(x) for x in picked)
                ctx_res = [r for r in ctx_res
                           if str((r.get("meta") or {}).get("source", "")) in picked] or ctx_res
            ctx = knowledge_base.format_context(ctx_res)
            # 调用层上下文压缩（检索之后、送模型之前）：只把最相关句子送给模型，省 token、降噪声。
            # 前端可在 ② 答疑 勾选「🗜️ 压缩上下文」（默认开）；检索无命中时自动跳过。
            comp_note = ""
            if data.get("compress", True) and config.KB_COMPRESS and ctx_res:
                try:
                    ctx_comp, cstats = knowledge_base.compress_context(q, ctx_res)
                    if ctx_comp:
                        ctx = ctx_comp
                        comp_note = " · 上下文已压缩（%d→%d token，保留 %d 句）" % (
                            cstats.get("orig_tokens", 0), cstats.get("comp_tokens", 0),
                            cstats.get("kept", 0))
                except Exception as e:
                    print("[ask] 上下文压缩失败，回退全量:", e)
            # 强制以用户选定语言作答；提问无论中英都要求先理解语义
            answer = qa_brain.answer_question(q, ctx, lang=_LANG_FORCE.get(lang))
            ans_audio = os.path.join(config.DIR_NARR, "answer_tmp.wav")
            ok = narrate.synth_one(answer, lang, ans_audio, seed=99, ref=resolve_voice(data.get("person") or None))
            shown = "\n".join(f"[{r.get('label','')}] {r['text'][:80]}…" for r in ctx_res[:3])
            self._json({
                "answer": answer,
                "audio": ("/api/audio?name=answer_tmp.wav") if ok else None,
                "ctx": shown,
                "comp": comp_note,
                "qlang": q_lang,
                # 若问题与某一页强相关，前端自动翻到该页
                "slide": _find_related_slide(q, ctx_res),
            })
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_qa_log(self, data):
        """答疑记录：增 / 列 / 清 / 总结 / 导出 / 入库。
        前端 ② 答疑 与 ⑤ 正式演示（领导打断）都来这里沉淀问答；
        ⑥ 答疑总结 据此生成《答疑汇总》并可选回灌知识库。"""
        try:
            act = (data.get("act") or "list").strip()
            if act == "add":
                recs = _load_qa_log()
                recs.append({
                    "id": int(time.time() * 1000),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "question": (data.get("question") or "").strip(),
                    "qlang": data.get("qlang", ""),
                    "answer": (data.get("answer") or "").strip(),
                    "slide_idx": data.get("slide_idx"),
                    "mode": data.get("mode", "答疑"),   # 答疑 / 演示中断
                    "sources": data.get("sources", ""),
                })
                _save_qa_log(recs)
                self._json({"ok": True, "records": recs}); return
            if act == "list":
                self._json({"ok": True, "records": _load_qa_log()}); return
            if act == "clear":
                _save_qa_log([])
                self._json({"ok": True, "records": []}); return
            if act == "summary":
                self._json({"ok": True, "summary": _summarize_qa(_load_qa_log())}); return
            if act == "export":
                text = data.get("text", "")
                if not text.strip():
                    self._json({"error": "空内容"}); return
                path = os.path.join(config.DIR_TEST,
                                    "答疑汇总_%s.md" % time.strftime("%Y%m%d_%H%M%S"))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self._json({"ok": True, "path": path}); return
            if act == "store":
                text = data.get("text", "")
                if not text.strip():
                    self._json({"error": "空内容"}); return
                tmp = os.path.join(config.DIR_TEST, "qa_summary_%d.md" % int(time.time()))
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                r = knowledge_base.ingest_document(tmp, KB_NAME, category="答疑汇总")
                self._json({"ok": True, "ingested": r,
                            "name": os.path.basename(tmp),
                            "total": r.get("total")}); return
            self._json({"error": "未知操作: " + act})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_asr(self, data):
        try:
            b64 = data.get("data", "")
            lang = data.get("lang", "zh")
            tmp = os.path.join(config.DIR_TEST, "asr_tmp.webm")
            with open(tmp, "wb") as f:
                f.write(base64.b64decode(b64))
            from asr_listen import transcribe
            text = transcribe(tmp, lang=lang)
            self._json({"text": text})
        except Exception as e:
            self._json({"text": f"（语音识别失败：{e}）"})

    def _handle_ingest(self, data):
        try:
            category = data.get("category", "")
            srv_name = data.get("name")
            if srv_name:
                # 服务端已存在的文件（如「制作 PPT」生成的 .pptx）直接复用，免上传
                tmp = os.path.join(config.DIR_TEST, os.path.basename(srv_name))
                if not (os.path.exists(tmp) and os.path.isfile(tmp)):
                    self._json({"error": "文件不存在：%s" % srv_name}); return
                ext = os.path.splitext(tmp)[1] or ".txt"
            else:
                b64 = data.get("data", "")
                ext = data.get("ext", ".txt")
                tmp = os.path.join(config.DIR_TEST, "ingest" + ext)
                with open(tmp, "wb") as f:
                    f.write(base64.b64decode(b64))
            global SLIDES
            if ext.lower() in (".pptx", ".ppt"):
                slides = pptx_parse.parse(tmp)
                SLIDES = _slides_to_urls(slides)
                SLIDES_META.update({"source": os.path.basename(tmp), "count": len(SLIDES),
                                    "source_path": tmp})
                _save_slides()
                # 原稿版式页图；同步渲染（该接口本就用于手动导入，可稍候）
                _render_slide_images(tmp)
                r = knowledge_base.ingest_pptx(tmp, KB_NAME)
            elif ext.lower() in (".pdf", ".doc", ".xls", ".rtf"):
                # 先用 doc_parse 抽文本（pdf 走 pypdf，老格式走 LibreOffice），再入向量库
                d = doc_parse.extract(tmp)
                txt_path = tmp + ".txt"
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(d["text"])
                r = knowledge_base.ingest_document(txt_path, KB_NAME, category=category)
                r["chars"] = len(d["text"])
            else:  # .txt .md .docx .xlsx
                r = knowledge_base.ingest_document(tmp, KB_NAME, category=category)
            self._json(r)
        except Exception as e:
            self._json({"error": str(e)})

    # ---------- 项目管理（顶层 project 概念：切换 / 新建 / 重命名 / 删除）----------
    def _handle_projects(self, data):
        """所有资源（讲稿版本/知识库/讲解音频/数字人/分身象库/问答日志）均隶属于某个项目。
        切换项目 = 重算 config 内全部数据目录 + 重载本项目讲稿。默认项目不可删除。"""
        try:
            act = (data.get("act") or "list").strip()
            if act == "list":
                self._json({"ok": True, "active": config.get_active_project(),
                            "projects": config.list_projects()}); return
            if act == "switch":
                pid = (data.get("id") or "").strip()
                if not pid or not os.path.isdir(os.path.join(config.PROJECTS_DIR, pid)):
                    self._json({"ok": False, "error": "项目不存在：%s" % pid}); return
                config.set_active_project(pid)
                _load_slides()
                self._json({"ok": True, "active": pid, "projects": config.list_projects(),
                            "slides": SLIDES, "meta": SLIDES_META}); return
            if act == "create":
                if PLAYBACK_MODE:
                    self._json({"ok": False, "error": "播放版不支持新建项目"}); return
                name = (data.get("name") or "").strip()
                if not name:
                    self._json({"ok": False, "error": "请输入项目名称"}); return
                pid = config.create_project(name)
                config.set_active_project(pid)
                _load_slides()
                self._json({"ok": True, "id": pid, "active": pid,
                            "projects": config.list_projects(),
                            "slides": SLIDES, "meta": SLIDES_META}); return
            if act == "rename":
                if PLAYBACK_MODE:
                    self._json({"ok": False, "error": "播放版不支持重命名项目"}); return
                pid = (data.get("id") or "").strip()
                name = (data.get("name") or "").strip()
                if not pid:
                    self._json({"ok": False, "error": "缺少项目 id"}); return
                if not name:
                    self._json({"ok": False, "error": "请输入新的项目名称"}); return
                if not config.rename_project(pid, name):
                    self._json({"ok": False, "error": "项目不存在：%s" % pid}); return
                self._json({"ok": True, "active": config.get_active_project(),
                            "projects": config.list_projects()}); return
            if act == "delete":
                if PLAYBACK_MODE:
                    self._json({"ok": False, "error": "播放版不支持删除项目"}); return
                pid = (data.get("id") or "").strip()
                if pid == config.DEFAULT_PROJECT_ID:
                    self._json({"ok": False, "error": "默认项目不可删除"}); return
                if not config.delete_project(pid):
                    self._json({"ok": False, "error": "项目不存在或删除失败：%s" % pid}); return
                # 若删的是当前项目，config 已回退到 default；重算目录并重载讲稿
                cur = config.get_active_project()
                config.set_active_project(cur, persist=False)
                _load_slides()
                self._json({"ok": True, "active": cur, "projects": config.list_projects(),
                            "slides": SLIDES, "meta": SLIDES_META}); return
            self._json({"ok": False, "error": "未知操作：" + act})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    # ---------- 分身管理（多人 × 多形象）----------
    def _handle_persons(self, data):
        try:
            act = (data.get("act") or "list").strip()
            if act == "list":
                self._json(persons.list_persons()); return
            if act == "add_person":
                _, pid = persons.add_person(data.get("name", "新分身"), data.get("note", ""))
                self._json({"ok": True, "id": pid, "persons": persons.list_persons()}); return
            if act == "rename_person":
                persons.rename_person(data.get("pid"), data.get("name", ""))
                self._json({"ok": True, "persons": persons.list_persons()}); return
            if act == "del_person":
                persons.delete_person(data.get("pid"))
                self._json({"ok": True, "persons": persons.list_persons()}); return
            if act == "add_avatar":
                # 图片以 base64 上传
                b64 = data.get("data", "")
                ext = data.get("ext", ".jpg")
                tmp = os.path.join(config.DIR_TEST, "portrait_tmp" + ext)
                with open(tmp, "wb") as f:
                    f.write(base64.b64decode(b64))
                _, aid = persons.add_avatar(data.get("pid"), data.get("name", "新形象"),
                                            tmp, data.get("scene", ""))
                self._json({"ok": True, "id": aid, "persons": persons.list_persons()}); return
            if act == "update_avatar":
                src = None
                if data.get("data"):
                    ext = data.get("ext", ".jpg")
                    src = os.path.join(config.DIR_TEST, "portrait_tmp" + ext)
                    with open(src, "wb") as f:
                        f.write(base64.b64decode(data["data"]))
                persons.update_avatar(data.get("pid"), data.get("aid"),
                                      name=data.get("name"), scene=data.get("scene"),
                                      src_path=src)
                self._json({"ok": True, "persons": persons.list_persons()}); return
            if act == "del_avatar":
                persons.delete_avatar(data.get("pid"), data.get("aid"))
                self._json({"ok": True, "persons": persons.list_persons()}); return
            self._json({"error": "未知操作: " + act})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_settings(self, data):
        try:
            if data:
                s = save_settings(data)
                # ⚠ 「选择分身」必须同步落到 persons.json，否则后端取声纹/形象时读到的
                #    还是旧 default → 界面选金子、语音却合成炎冰（v2.6.1 修复）。
                if data.get("person") or data.get("avatar"):
                    persons.set_active(data.get("person"), data.get("avatar"))
                self._json({"ok": True, "settings": s,
                            "active": persons.active()})
            else:
                self._json({"ok": True, "settings": load_settings(),
                            "active": persons.active()})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_scope(self, data):
        """切换「当前作用域」：分身 + 形象。全系统唯一入口（声音/形象/人称一起变）。"""
        try:
            pid, aid = data.get("person"), data.get("avatar")
            if not pid and not aid:
                return self._json({"ok": True, "active": persons.active()})
            _, ok = persons.set_active(pid, aid)
            if not ok:
                return self._json({"ok": False, "error": "分身不存在：%s" % pid,
                                   "active": persons.active()})
            save_settings({"person": persons.active().get("person", ""),
                           "avatar": persons.active().get("avatar", "")})
            act = persons.active()
            self._json({"ok": True, "active": act,
                        "voice": os.path.basename(resolve_voice(act["person"])),
                        "avatar_file": os.path.basename(
                            persons.resolve(act["person"], act["avatar"]) or "")})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_browse(self, data):
        """目录浏览：给「设置 → 默认文件夹」的文件夹选择器用。

        返回当前路径下的子目录列表 + 常用锚点（我的文档/下载/桌面/磁盘根目录），
        让用户能点选而非手敲绝对路径。只列目录，不列文件，避免暴露无关内容。
        """
        try:
            cur = data.get("path") or ""
            if not cur:
                cur = os.path.expanduser("~")
            cur = os.path.abspath(cur)
            dirs = []
            if os.path.isdir(cur):
                for nm in sorted(os.listdir(cur)):
                    full = os.path.join(cur, nm)
                    try:
                        if os.path.isdir(full) and not nm.startswith("."):
                            dirs.append({"name": nm, "path": full})
                    except Exception:
                        continue
            anchors = []
            home = os.path.expanduser("~")
            for label, p in [("主目录", home), ("文档", os.path.join(home, "Documents")),
                             ("下载", os.path.join(home, "Downloads")),
                             ("桌面", os.path.join(home, "Desktop")),
                             ("图片", os.path.join(home, "Pictures"))]:
                if os.path.isdir(p):
                    anchors.append({"name": label, "path": p})
            for d in "CDEFG":
                p = d + ":\\"
                if os.path.isdir(p):
                    anchors.append({"name": d + " 盘", "path": p})
            parent = os.path.dirname(cur)
            self._json({"ok": True, "current": cur, "parent": parent if parent != cur else "",
                        "dirs": dirs, "anchors": anchors})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_llm_config(self, data):
        """大模型接入配置：GET(无 data) 时返回当前配置；POST 时保存。
        支持本地（Ollama 默认）与线上 OpenAI-compatible 服务（DeepSeek / 通义 / vLLM 等）。"""
        try:
            if data:
                self._json({"ok": True, "config": llm_client.save_llm_config(data)})
            else:
                self._json({"ok": True, "config": llm_client.load_llm_config()})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_make_ppt(self, data):
        """制作 PPT（MVP 全链路）：

        stage=analyze → 只做需求解析（判断信息是否够、给出追问与建议参数）
        默认          → 需求解析 →（可选）检索本地知识库取事实依据 → 大模型生成
                        含版式/图标/图表的大纲 → 按配色主题渲染落盘 .pptx
        """
        try:
            topic = (data.get("topic") or "").strip()
            style = (data.get("style") or "商务简洁").strip() or "商务简洁"
            lang = data.get("lang") or "中文"
            audience = (data.get("audience") or "").strip()
            scene = (data.get("scene") or "").strip()
            industry = (data.get("industry") or "").strip()
            try:
                pages = max(3, min(40, int(data.get("pages") or 6)))
            except Exception:
                pages = 6
            theme = (data.get("theme") or "blue").strip() or "blue"
            # v2.6：模板可以是内置配色 key，也可以是用户在「模板管理」里导入的自定义 .pptx
            template = (data.get("template") or "").strip()
            use_kb = data.get("use_kb", True)
            with_image = bool(data.get("with_image"))
            export_pdf = bool(data.get("export_pdf"))
            # 交付物：PPT 之外是否同步产出「书面文档」及其格式（docx / pdf）
            doc_formats = tuple(f for f in (data.get("doc_formats") or [])
                                if f in ("docx", "pdf"))

            # ① 需求解析（不填主题也能先跑，给出追问）
            need = make_ppt.analyze_need(topic, audience=audience, scene=scene,
                                         industry=industry, pages=pages, style=style)
            if (data.get("stage") or "") == "analyze":
                self._json({"ok": True, "need": need}); return
            if not topic:
                self._json({"error": "请填写 PPT 主题", "need": need}); return
            # 太模糊且用户没要求强制生成 → 返回追问，让前端引导补充
            if need["level"] == "vague" and not data.get("force"):
                self._json({"ok": False, "need_more": True, "need": need,
                            "error": "描述还不够具体，请先补充下面几项（也可直接点「按建议生成」）"}); return

            # ② 生成大纲（可带本地知识库依据）
            outline, meta = make_ppt.gen_outline(
                topic, style=style, lang=lang, pages=pages,
                audience=audience, scene=scene, industry=industry,
                use_kb=use_kb, kb_name=KB_NAME)

            # ③ 可选 ComfyUI 配图 + 渲染
            slides = []
            for i, o in enumerate(outline):
                img = None
                if with_image:
                    img = _comfyui_gen_image("%s，%s，扁平插画风格，简洁" % (topic, o.get("title", "")),
                                             seed=1000 + i)
                slides.append({"title": o.get("title", "第%d页" % (i + 1)),
                               "bullets": o.get("bullets", []), "image": img,
                               "layout": o.get("layout", "bullets"),
                               "icons": o.get("icons", []),
                               "chart": o.get("chart") or {"type": "none"},
                               "source": o.get("source", "")})
            # 产出统一落到「设置 → 默认文件夹 → 导出目录」，便于用户 afterward 直接找得到
            export_dir = app_settings.folders().get("export") or config.DIR_TEST
            os.makedirs(export_dir, exist_ok=True)
            os.makedirs(config.DIR_TEST, exist_ok=True)
            safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", topic)[:30] or "ppt"
            stamp = int(time.time())

            artifacts = []          # [{kind, name, download, size}]
            warnings = []           # 人话告警（如 LibreOffice 缺失）

            # ---- (a) PPT 本体 ----
            out_path = os.path.join(export_dir, "%s_%d.pptx" % (safe, stamp))
            pptx_write.write_pptx(out_path, slides, theme=theme)
            # 自定义模板：换皮（继承用户模板的字体/主色/母版）
            if template and template not in ("blue", "gray", "dark"):
                tp = ppt_templates.template_path(template)
                if tp:
                    ppt_templates.apply_template(out_path, tp, out_path)
                else:
                    warnings.append("所选模板文件已丢失，已改用内置配色「%s」生成" % theme)
            artifacts.append(_artifact("PPT", out_path))

            # ---- (b) PPT 同步导出 PDF（讲稿留存 / 发给无法编辑 PPT 的人）----
            if export_pdf:
                ok, pdf_path, err = doc_write.convert_to_pdf(out_path, export_dir)
                if ok:
                    artifacts.append(_artifact("PPT的PDF", pdf_path))
                else:
                    warnings.append("PPT 转 PDF 未成功：" + err)

            # ---- (c) 书面文档（doc / pdf）：与 PPT 内容不同，要详实而非要点 ----
            if doc_formats:
                blocks = _gen_written_doc(topic, slides, audience=audience,
                                          scene=scene, industry=industry,
                                          lang=lang, use_kb=use_kb)
                dres = doc_write.write_doc("%s_书面稿_%d" % (safe, stamp),
                                           "%s" % topic, blocks, export_dir,
                                           formats=doc_formats)
                if dres.get("docx") and "docx" in doc_formats:
                    artifacts.append(_artifact("书面文档DOC", dres["docx"]))
                if dres.get("pdf") and "pdf" in doc_formats:
                    artifacts.append(_artifact("书面文档PDF", dres["pdf"]))
                warnings.extend(dres.get("errors") or [])

            self._json({"ok": True, "path": os.path.basename(out_path),
                        "name": os.path.basename(out_path),
                        "download": "/api/download?name=" + os.path.basename(out_path),
                        "artifacts": artifacts,
                        "warnings": warnings,
                        "export_dir": export_dir,
                        "slides": [{"title": s["title"], "bullets": s["bullets"],
                                    "layout": s["layout"], "icons": s["icons"],
                                    "chart": s["chart"], "source": s["source"]}
                                   for s in slides],
                        "pages": len(slides), "theme": theme,
                        "gen": meta, "need": need,
                        "kb_used": bool(use_kb)})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_sources(self, data):
        try:
            self._json({"sources": knowledge_base.list_sources(KB_NAME)})
        except Exception as e:
            self._json({"error": str(e)})

    def _handle_kb_backup(self, data):
        """导出知识库备份（.mymekb）：把 kb_data/ 整目录打包，返回下载链接与统计。"""
        try:
            out = knowledge_base.export_backup(KB_NAME)
            if not out.get("ok"):
                self._json(out); return
            self._json({"ok": True,
                        "zip": "/api/download?name=" + out["name"],
                        "path": out["path"], "name": out["name"],
                        "size": out["size"], "size_mb": out["size_mb"],
                        "files": out["files"], "chunks": out["chunks"],
                        "manifest": out.get("manifest")})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_kb_rebuild(self, data):
        """知识库重建：stats（预览前后对比）/ run（执行去重 / 优化 / 压缩）。"""
        try:
            act = (data.get("act") or "stats").strip()
            if act == "stats":
                self._json({"ok": True, "stats": knowledge_base.kb_stats(KB_NAME)})
                return
            if act == "run":
                self._json(knowledge_base.rebuild_kb(KB_NAME))
                return
            self._json({"ok": False, "error": "未知操作：%s" % act})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_avatar(self, data):
        """视频数字人：热 TTS(按分身选声纹) 合成声音 + 热/冷 LivePortrait 音频驱动口型。
        入参：{text, lang, image(base64 可选), avatar(分身 id 可选), seed}。
        肖像优先级：上传图(base64) > 指定分身(avatar) > 默认分身 > 旧 portrait.png > 示例脸。
        返回 {ok, audio, video, note, avatar}。"""
        try:
            text = (data.get("text") or "").strip()
            lang = data.get("lang", "中文")
            img_b64 = data.get("image")
            persona = data.get("avatar") or None
            person = data.get("person") or None
            seed = int(data.get("seed", 99))
            uid = uuid.uuid4().hex[:12]   # 本次请求唯一标识：上传图 / 输出 mp4 均带它，避免并发串用
            if not text:
                self._json({"error": "空台词"}); return
            if img_b64:
                img_path = os.path.join(config.DIR_TEST, "avatar_src_%s.png" % uid)
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(img_b64))
            else:
                # 多人多形象：优先按 person/avatar 解析；回退旧象库，再回退默认肖像
                img_path = persons.resolve(person, persona) or resolve_avatar(persona)
            if not img_path or not os.path.exists(img_path):
                img_path = AVATAR_IMAGE if os.path.exists(AVATAR_IMAGE) else AVATAR_IMAGE_DEFAULT
            out_mp4 = os.path.join(config.DIR_AVATAR, "avatar_%s.mp4" % uid)
            res = _make_avatar(text, lang, img_path, seed, out_mp4, person=person,
                              skip_audio=bool(data.get("skip_audio")))
            res["avatar"] = persona or "default"
            self._json(res)
        except Exception as e:
            self._json({"error": str(e)})


def _start_tts_service():
    """后台拉起常驻 TTS 服务（嵌式 python），让模型只加载一次、之后合成秒级。
    若服务已在运行则跳过。失败也不影响主程序（narrate 会自动回退冷启动子进程）。"""
    if not USE_TTS_SERVICE or not os.path.exists(EMBEDDED_PY):
        return
    try:
        urllib.request.urlopen(TTS_SERVICE_URL + "/ready", timeout=2)
        print(f"[app] TTS 服务已在运行：{TTS_SERVICE_URL}（热合成已就绪）")
        return
    except Exception:
        pass
    log = os.path.join(config.DIR_TEST, "tts_service.log")
    svc = config.svc_script("tts_service.py")
    cmd = [EMBEDDED_PY, svc,
           "--host", "127.0.0.1", "--port", str(TTS_SERVICE_PORT), "--eager"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with open(log, "w", encoding="utf-8") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=flags, cwd=os.path.dirname(svc))
        print(f"[app] 已后台拉起常驻 TTS 服务（首次加载模型约 1-2 分钟，之后合成秒级）：{TTS_SERVICE_URL}")
    except Exception as e:
        print(f"[app] TTS 服务启动失败，将回退冷启动子进程：{e}")


def _start_avatar_service():
    """后台拉起常驻数字人服务（嵌式 python，热 LivePortrait）。
    若已在运行则跳过；失败也不影响主程序（/api/avatar 会自动回退冷启动子进程或优雅降级）。"""
    if not USE_AVATAR_SERVICE or not os.path.exists(EMBEDDED_PY):
        return
    try:
        urllib.request.urlopen(AVATAR_SERVICE_URL + "/ready", timeout=2)
        print(f"[app] 数字人服务已在运行：{AVATAR_SERVICE_URL}（热推理已就绪）")
        return
    except Exception:
        pass
    log = os.path.join(config.DIR_TEST, "avatar_service.log")
    svc = config.svc_script("avatar_service.py")
    cmd = [EMBEDDED_PY, svc,
           "--host", "127.0.0.1", "--port", str(AVATAR_SERVICE_PORT), "--eager"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with open(log, "w", encoding="utf-8") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=flags, cwd=os.path.dirname(svc))
        print(f"[app] 已后台拉起常驻数字人服务（首次加载 LivePortrait 模型约 1 分钟）：{AVATAR_SERVICE_URL}")
    except Exception as e:
        print(f"[app] 数字人服务启动失败，/api/avatar 将回退冷启动或降级：{e}")


def _child_watchdog(interval=30):
    """常驻子服务保活：TTS(8777) / 数字人(8778) 偶发自行退出会导致语音与视频分身失效。
    每 interval 秒探一次 /ready，掉了就重新拉起。守护线程，不影响主服务。"""
    import time as _t
    _t.sleep(180)          # 启动后先给足模型加载时间（TTS 1-2 分钟 / 数字人约 1 分钟）
    miss = {"tts": 0, "avatar": 0}
    while True:
        _t.sleep(interval)
        try:
            if USE_TTS_SERVICE and os.path.exists(EMBEDDED_PY):
                try:
                    urllib.request.urlopen(TTS_SERVICE_URL + "/ready", timeout=3)
                    miss["tts"] = 0
                except Exception:
                    miss["tts"] += 1
                    # 连续两次探不到才判定掉线，避免模型加载期误判后重复拉起
                    if miss["tts"] >= 2:
                        print("[watchdog] TTS 服务已掉线，正在重新拉起…", flush=True)
                        _start_tts_service(); miss["tts"] = 0; _t.sleep(120)
            if USE_AVATAR_SERVICE and os.path.exists(EMBEDDED_PY):
                try:
                    urllib.request.urlopen(AVATAR_SERVICE_URL + "/ready", timeout=3)
                    miss["avatar"] = 0
                except Exception:
                    miss["avatar"] += 1
                    if miss["avatar"] >= 2:
                        print("[watchdog] 数字人服务已掉线，正在重新拉起…", flush=True)
                        _start_avatar_service(); miss["avatar"] = 0; _t.sleep(120)
        except Exception as e:
            print("[watchdog] 巡检异常：%s" % e, flush=True)


def _cur_person_label(person=None):
    """当前默认分身名（用于进度/提示文案）。取不到时回落为中性词，避免多分身场景写死人名。"""
    try:
        if person:
            for p in (persons.load().get("persons") or []):
                if p.get("id") == person:
                    return p.get("name") or person
        return (get_current_persona() or {}).get("name") or "分身"
    except Exception:
        return "分身"


def _silent_wav(out_wav, dur=3.0, sr=16000):
    """纯标准库生成静音 wav（不依赖 ffmpeg）：供「跳过音频」时驱动一个嘴部闭合、
    仅保留自然微动的分身视频。失败返回 None。"""
    try:
        import wave
        with wave.open(out_wav, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            wf.writeframes(b"\x00\x00" * int(sr * dur))
        return out_wav if os.path.exists(out_wav) else None
    except Exception:
        return None


def _make_avatar(text, lang, image_path, seed, out_mp4, person=None, skip_audio=False):
    """声音复刻 + 视频数字人：热 TTS 合成分身声音（按 person 选声纹，默认炎冰）→ 热/冷 LivePortrait 口型驱动。
    返回 dict: {ok, audio, video, note}。任一环节失败都尽量降级。
    skip_audio=True：跳过 TTS，用静音轨驱动——只保留自然微动、嘴部闭合的分身画面，
    解决「TTS 失败导致整段生成失败」以及「只想出画面不要声音」的需求。"""
    from config import TTS_SEED_BASE
    os.makedirs(config.DIR_AVATAR, exist_ok=True)
    # 隔离：每次调用用唯一 uid 命名临时音频（不再共享 avatar_audio_tmp.wav）。
    # 历史上"界面选金子、语音却炎冰"的根因之一，就是共享临时文件被并发覆盖。
    # 注：该临时音频会被 /api/audio 回放，故保留不删；仅以唯一名隔离。
    uid = uuid.uuid4().hex[:12]
    res = {"ok": True, "audio": None, "video": None, "note": ""}
    if skip_audio:
        # 静音时长按台词长度估（无配音时也给个合理展示时长），下限 2 秒
        dur = max(2.0, len(text) * 0.22)
        ans_audio = os.path.join(config.DIR_AVATAR, "avatar_silent_%s.wav" % uid)
        if not _silent_wav(ans_audio, dur):
            ans_audio = None
    else:
        ans_audio = os.path.join(config.DIR_AVATAR, "avatar_audio_%s.wav" % uid)
        ok_audio = narrate.synth_one(text, lang, ans_audio, seed=seed or 99, ref=resolve_voice(person))
        if not ok_audio:
            return {"ok": False, "audio": None, "video": None,
                    "note": "分身声音合成失败（TTS 服务/子进程不可用）。可勾选「跳过音频」仅生成画面。"}
        res["audio"] = "/api/audio?name=" + os.path.basename(ans_audio)
    if not ans_audio or not os.path.exists(ans_audio):
        return {"ok": False, "audio": None, "video": None,
                "note": "跳过的音频轨无法生成（静音 wav 写出失败）。"}
    # 1) 优先走常驻数字人服务（热 LivePortrait，模型常驻）
    if USE_AVATAR_SERVICE:
        try:
            if urllib.request.urlopen(AVATAR_SERVICE_URL + "/ready", timeout=2).status == 200:
                payload = json.dumps({
                    "audio": ans_audio, "image": image_path, "out": out_mp4,
                    "res": AVATAR_RES,
                }).encode("utf-8")
                req = urllib.request.Request(AVATAR_SERVICE_URL + "/avatar", data=payload,
                                            headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    rr = json.loads(r.read().decode("utf-8"))
                if rr.get("ok") and os.path.exists(out_mp4):
                    res["video"] = "/api/video?name=" + os.path.basename(out_mp4)
                    if rr.get("note"):
                        res["note"] = rr["note"]
                    return res
                res["note"] = (rr.get("note") or "数字人服务推理失败") + "（尝试回退冷启动）"
        except Exception as e:
            res["note"] = f"数字人服务不可用，回退冷启动：{e}"
    # 2) 回退：每次冷启动子进程跑 LivePortrait（avatar.drive_avatar 内部处理缺失降级）
    try:
        dr = avatar.drive_avatar(image_path, ans_audio, out_mp4, resolution=AVATAR_RES, prepare=True)
        if dr.get("ok") and os.path.exists(out_mp4):
            res["video"] = "/api/video?name=" + os.path.basename(out_mp4)
            if dr.get("note"):
                res["note"] = (res.get("note") or "") + "\n" + dr["note"]
            return res
        else:
            res["note"] = (res.get("note") or "") + "\n" + (dr.get("note") or "LivePortrait 未就绪")
    except Exception as e:
        res["note"] = (res.get("note") or "") + f"\nLivePortrait 推理异常：{e}"
    # 声音已合成，视频缺失：仍返回音频，提示视频需安装 LivePortrait
    if skip_audio:
        res["ok"] = False
    else:
        res["ok"] = True
    return res


# ================= ⑤-增强：分身视频讲解（逐页口型同步，按版本缓存） =================
def _resolve_slide_audio(audio):
    """把 slide.audio（可能是 /api/audio?name=xxx 或本地相对路径）解析为本地 wav 绝对路径。"""
    if not audio:
        return None
    s = str(audio)
    # 优先解析 /api/audio?name=xxx 的查询参数（历史版本只取 ? 前面，会解析成 'audio' 而全部失败）
    if "name=" in s:
        s = s.split("name=", 1)[1].split("&")[0].strip()
    name = os.path.basename(s.split("?")[0].replace("\\", "/").replace("\n", "/"))
    for root, _d, files in os.walk(config.DIR_NARR):
        if name in files:
            return os.path.join(root, name)
    cand = s.replace("\\", "/").split("?")[0]
    if os.path.isabs(cand) and os.path.exists(cand):
        return cand
    p2 = os.path.join(HERE, cand)
    if os.path.exists(p2):
        return p2
    return None


def _demo_av_basename(version, idx, persona=None, person=None):
    vs = "".join(c if c.isalnum() else "_" for c in str(version))
    # 缓存文件名必须唯一标识 (版本, 页, 形象, 声纹)：换声纹(person)必须重生成，
    # 否则会端出别人(另一分身)的缓存视频——多分身"音图视频混用"的根因之一。
    # 非默认分身/声纹才加段，默认沿用旧名以兼容已缓存的 demo_av_<版本>_<页>.mp4。
    seg_p = person if (person and person != "default") else ""
    seg_a = persona if (persona and persona != "default") else ""
    if seg_p or seg_a:
        return f"demo_av_{vs}_{seg_p}_{seg_a}_{idx}.mp4"
    return f"demo_av_{vs}_{idx}.mp4"


def _demo_av_path(version, idx, persona=None, person=None):
    return os.path.join(config.DIR_AVATAR, _demo_av_basename(version, idx, persona, person))


def _demo_av_status(version):
    """扫描 config.DIR_AVATAR 中该版本已缓存的逐页口型视频，返回 {idx_str: video_url}。"""
    vs = "".join(c if c.isalnum() else "_" for c in str(version))
    pref = f"demo_av_{vs}_"
    out = {}
    if os.path.isdir(config.DIR_AVATAR):
        for fn in os.listdir(config.DIR_AVATAR):
            if fn.startswith(pref) and fn.endswith(".mp4"):
                try:
                    ix = int(fn[len(pref):-4])
                except Exception:
                    continue
                fp = os.path.join(config.DIR_AVATAR, fn)
                if os.path.getsize(fp) > 0:
                    out[str(ix)] = "/api/video?name=" + fn
    return out


def _make_avatar_video(audio_path, image_path, out_mp4, motion=None):
    """给定已合成讲解音频 + 肖像图，产出口型同步 mp4（讲解声纹 + LivePortrait 或 Sonic）。
    motion：微动强度（1.0=自然含眨眼/眼珠/微表情/头部微摆，0=仅口型）。
    AVATAR_ENGINE=sonic 时优先走扩散式 Sonic（经 ComfyUI），失败/不可用自动回退 LivePortrait。
    优先常驻数字人服务（热推理），否则冷启动子进程；失败返回 ok=False 及原因。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    prepared = out_mp4 + ".prep.wav"
    try:
        prepared = avatar.prepare_audio(audio_path, prepared)
    except Exception:
        prepared = audio_path
    res = {"ok": False, "out_mp4": out_mp4, "note": ""}
    # —— 扩散式引擎（Sonic）：优先尝试，失败/不可用则回退 LivePortrait ——
    if AVATAR_ENGINE == "sonic" and not PLAYBACK_MODE:
        if not avatar_sonic.sonic_available(url=SONIC_URL, timeout=4):
            # 可选自动拉起本机嵌式 ComfyUI（MYME_SONIC_AUTOSTART=1 时）
            if os.environ.get("MYME_SONIC_AUTOSTART", "0") == "1":
                avatar_sonic.start_comfyui(timeout=180)
        if avatar_sonic.sonic_available(url=SONIC_URL, timeout=4):
            sr = avatar_sonic.drive_avatar_sonic(
                image_path, prepared, out_mp4, url=SONIC_URL, timeout=SONIC_TIMEOUT)
            if sr.get("ok") and os.path.exists(out_mp4):
                res["ok"] = True
                res["note"] = sr.get("note", "")
                return res
            res["note"] = (res.get("note") or "") + "\n[Sonic 不可用，回退 LivePortrait] " + (sr.get("note") or "")
        else:
            res["note"] = (res.get("note") or "") + "\n[ComfyUI/Sonic 未就绪，回退 LivePortrait]"
    if USE_AVATAR_SERVICE:
        try:
            if urllib.request.urlopen(AVATAR_SERVICE_URL + "/ready", timeout=2).status == 200:
                payload = json.dumps({"audio": prepared, "image": image_path,
                                      "out": out_mp4, "res": AVATAR_RES,
                                      "motion": motion}).encode("utf-8")
                req = urllib.request.Request(AVATAR_SERVICE_URL + "/avatar", data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=600) as r:
                    rr = json.loads(r.read().decode("utf-8"))
                if rr.get("ok") and os.path.exists(out_mp4):
                    res["ok"] = True
                    res["note"] = rr.get("note", "")
                    return res
                res["note"] = (rr.get("note") or "数字人服务推理失败") + "（尝试回退冷启动）"
        except Exception as e:
            res["note"] = f"数字人服务不可用，回退冷启动：{e}"
    try:
        dr = avatar.drive_avatar(image_path, prepared, out_mp4,
                                 resolution=AVATAR_RES, prepare=False, motion=motion)
        if dr.get("ok") and os.path.exists(out_mp4):
            res["ok"] = True
            if dr.get("note"):
                res["note"] = (res.get("note") or "") + "\n" + dr["note"]
            return res
        else:
            res["note"] = (res.get("note") or "") + "\n" + (dr.get("note") or "LivePortrait 未就绪")
    except Exception as e:
        res["note"] = (res.get("note") or "") + f"\nLivePortrait 推理异常：{e}"
    return res


def _handle_demo_avatar_video(self, data):
    """逐页分身视频：用某页已合成的炎冰讲解音频驱动肖像口型，产出 mp4 并按(版本,页)缓存。
    入参：{idx, version, person, avatar}。返回 {ok, video, cached, idx, note|error}。"""
    try:
        idx = int(data.get("idx"))
        version = data.get("version") or CURRENT_VERSION
        person = data.get("person") or None
        persona = data.get("avatar") or None
        # 关键：按请求的版本取该版本的页数据（SLIDES 只是"当前版本"的展开态，
        # 直接用会让 train2026 等其它版本生成出 default 的口型视频）
        ver = VERSIONS.get(version) if isinstance(VERSIONS, dict) else None
        pool = (ver or {}).get("slides") if ver else None
        sl = next((s for s in (pool or SLIDES) if s.get("idx") == idx), None)
        if not sl:
            self._json({"ok": False, "error": "无此页", "idx": idx}); return
        audio_path = _resolve_slide_audio(sl.get("audio"))
        if not audio_path or not os.path.exists(audio_path):
            self._json({"ok": False, "error": "本页无讲解音频", "idx": idx}); return
        img = persons.resolve(person, persona) or resolve_avatar(persona)
        if not img or not os.path.exists(img):
            img = AVATAR_IMAGE if os.path.exists(AVATAR_IMAGE) else AVATAR_IMAGE_DEFAULT
        out_mp4 = _demo_av_path(version, idx, persona, person)
        if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
            self._json({"ok": True, "video": "/api/video?name=" + os.path.basename(out_mp4),
                        "cached": True, "idx": idx}); return
        res = _make_avatar_video(audio_path, img, out_mp4)
        if res.get("ok") and os.path.exists(out_mp4):
            self._json({"ok": True, "video": "/api/video?name=" + os.path.basename(out_mp4),
                        "cached": False, "idx": idx, "note": res.get("note")})
        else:
            self._json({"ok": False, "error": res.get("note") or "生成失败", "idx": idx})
    except Exception as e:
        self._json({"error": str(e)})


# ================= ⑤-增强：整场连续数字分身视频（口型+表情+肢体微动） =================
def _wav_duration(wav):
    """读取 wav 精确时长（秒）；失败返回 0.0。"""
    try:
        import wave
        with wave.open(wav, "rb") as wf:
            fr = wf.getframerate()
            nf = wf.getnframes()
            return nf / fr if fr else 0.0
    except Exception:
        return 0.0


def _make_silence(out_wav, dur=0.4, sr=24000):
    """生成静音 wav（音频兜底 / 页间停顿用）。"""
    try:
        subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=%d:cl=mono" % sr,
                        "-t", "%.3f" % dur, "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le",
                        out_wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_wav if os.path.exists(out_wav) else None
    except Exception:
        return None


def _probe_av_params(path):
    """取视频首段流参数（v_w,v_h,v_fps,v_pixfmt,a_sr,a_ch），失败返回 None。
    用于让页间静帧片段与逐页口型视频参数完全一致，否则 concat(-c copy) 会因
    分辨率/帧率/采样率不匹配而失败，连续视频会静默降级为纯音频。"""
    try:
        out = subprocess.run([FFMPEG, "-v", "error", "-i", path,
                              "-show_entries",
                              "stream=width,height,r_frame_rate,pix_fmt,sample_rate,channels",
                              "-of", "json"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        d = json.loads(out.stdout or b"{}")
        v = a = None
        for s in d.get("streams", []):
            if s.get("codec_type") == "video" and v is None:
                v = s
            elif s.get("codec_type") == "audio" and a is None:
                a = s
        if not v:
            return None
        fr = (v.get("r_frame_rate") or "25/1").split("/")
        fps = float(fr[0]) / float(fr[1]) if len(fr) == 2 and int(fr[1]) else 25.0
        return {"w": int(v.get("width", 960)), "h": int(v.get("height", 1280)),
                "fps": round(fps, 3), "pix": v.get("pix_fmt", "yuv420p"),
                "sr": int((a or {}).get("sample_rate", 16000)),
                "ch": int((a or {}).get("channels", 1))}
    except Exception:
        return None


def _make_gap_clip(out_mp4, dur, img, v_w=960, v_h=1280, v_fps=25, v_pix="yuv420p",
                  a_sr=16000, a_ch=1):
    """页间停顿静帧片段：肖像静帧 + 静音，时长 dur 秒（自然呼吸停顿，避免页间生硬跳切）。
    视频分辨率/帧率/像素格式与音频采样率/声道必须与逐页口型视频完全一致，否则 concat(-c copy) 失败。"""
    try:
        if not (img and os.path.exists(img)):
            img = AVATAR_IMAGE if os.path.exists(AVATAR_IMAGE) else AVATAR_IMAGE_DEFAULT
        vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
              "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (v_w, v_h, v_w, v_h))
        cl = "mono" if a_ch <= 1 else "stereo"
        subprocess.run([FFMPEG, "-y", "-loop", "1", "-i", img,
                        "-f", "lavfi", "-i", "anullsrc=r=%d:cl=%s" % (a_sr, cl),
                        "-t", "%.3f" % dur, "-vf", vf,
                        "-c:v", "libx264", "-pix_fmt", v_pix, "-r", "%.3f" % v_fps,
                        "-c:a", "aac", "-ar", str(a_sr), "-ac", str(a_ch),
                        "-shortest", out_mp4],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_mp4 if (os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0) else None
    except Exception:
        return None


def _demo_av_full_basename(version):
    vs = "".join(c if c.isalnum() else "_" for c in str(version))
    return "demo_av_full_%s" % vs


def _demo_av_full_path(version):
    return os.path.join(config.DIR_AVATAR, _demo_av_full_basename(version) + ".mp4")


def _demo_av_full_dur(version):
    return os.path.join(config.DIR_AVATAR, _demo_av_full_basename(version) + "_dur.json")


def _concat_videos(seg_list, out_mp4):
    """把多段 mp4（同编码 libx264/aac）无缝拼接为一条（concat demuxer + 流拷贝）。"""
    if not seg_list:
        return False
    if len(seg_list) == 1:
        try:
            shutil.copy(seg_list[0], out_mp4)
            return os.path.exists(out_mp4)
        except Exception:
            return False
    list_file = out_mp4 + ".concat.txt"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for s in seg_list:
                f.write("file '%s'\n" % os.path.abspath(s))
        subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                        "-c", "copy", out_mp4],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.remove(list_file)
        except Exception:
            pass
    if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0:
        return True
    # 兜底：stream copy 因参数不一致（分辨率/帧率/采样率/声道/编码 profile）失败时，
    # 用 concat 滤镜统一重编码（缩放至 960x1280 + 16k 单声道），保证连续视频一定产出。
    try:
        inp = []
        for s in seg_list:
            inp += ["-i", s]
        n = len(seg_list)
        vscale = "".join(
            "[%d:v]scale=960:1280:force_original_aspect_ratio=decrease,"
            "pad=960:1280:(ow-iw)/2:(oh-ih)/2,setsar=1[v%d];" % (i, i)
            for i in range(n))
        fc = vscale + "concat=n=%d:v=1:a=1[outv][outa]" % n
        subprocess.run([FFMPEG, "-y"] + inp + [
            "-filter_complex", fc, "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-ar", "16000", "-ac", "1", out_mp4],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0


def _ensure_page_avatar(version, idx, sl, img, motion, persona=None, person=None):
    """确保某页口型视频已生成（复用缓存）。返回本地 mp4 路径或 None。
    缓存键含 (version,idx,persona,person)，换分身/声纹必重生成，杜绝跨分身串视频。"""
    out = _demo_av_path(version, idx, persona, person)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    ap = _resolve_slide_audio(sl.get("audio"))
    if not ap or not os.path.exists(ap):
        return None
    res = _make_avatar_video(ap, img, out, motion=motion)
    if res.get("ok") and os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    return None


def _build_full_audio(version, gap=0.4):
    """拼整场连续讲解音频（页间补静音），返回 (out_wav, timeline, total)。音频兜底用。"""
    ver = VERSIONS.get(version) if isinstance(VERSIONS, dict) else None
    pool = (ver or {}).get("slides") if ver else None
    slides = sorted(pool or SLIDES, key=lambda s: s.get("idx", 0))
    segs, timeline, cum = [], {}, 0.0
    tmp = os.path.join(config.DIR_AVATAR, "_full_tmp")
    os.makedirs(tmp, exist_ok=True)
    for sl in slides:
        idx = sl.get("idx")
        timeline[str(idx)] = round(cum, 3)
        ap = _resolve_slide_audio(sl.get("audio"))
        if ap and os.path.exists(ap):
            segs.append(ap)
            cum += _wav_duration(ap)
        else:
            sil = _make_silence(os.path.join(tmp, "sil_%s.wav" % idx), dur=1.0)
            if sil:
                segs.append(sil)
            cum += 1.0
        if gap and gap > 0:
            sgp = _make_silence(os.path.join(tmp, "gapsil_%s.wav" % idx), dur=gap)
            if sgp:
                segs.append(sgp)
            cum += gap
    out = os.path.join(config.DIR_AVATAR, _demo_av_full_basename(version) + ".wav")
    narrate.concat_audio([s for s in segs if s], out, fade=0.0)
    return (out if os.path.exists(out) else None), timeline, round(cum, 3)


def _build_full_avatar(version, person=None, persona=None, motion=None, gap=0.4, force=False):
    """生成整场连续数字分身视频：逐页口型视频（复用缓存）→ 插入页间静帧 → 无缝拼接。
    产出 demo_av_full_{vs}.mp4 + demo_av_full_{vs}_dur.json（时间线：每页起始秒数）。
    任何页缺少视频会自动以静帧停顿占位，保证时间线连续；完全无可用页则降级音频。
    返回 dict: {ok, video|audio, dur, total, cached, note}。"""
    out_mp4 = _demo_av_full_path(version)
    dur_file = _demo_av_full_dur(version)
    if (not force) and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0 and os.path.exists(dur_file):
        with open(dur_file, "r", encoding="utf-8") as f:
            dd = json.load(f)
        return {"ok": True, "video": "/api/video?name=" + os.path.basename(out_mp4),
                "dur": dd.get("timeline"), "total": dd.get("total"), "cached": True}
    ver = VERSIONS.get(version) if isinstance(VERSIONS, dict) else None
    pool = (ver or {}).get("slides") if ver else None
    slides = sorted(pool or SLIDES, key=lambda s: s.get("idx", 0))
    img = persons.resolve(person, persona) or resolve_avatar(persona)
    if not img or not os.path.exists(img):
        img = AVATAR_IMAGE if os.path.exists(AVATAR_IMAGE) else AVATAR_IMAGE_DEFAULT
    timeline, segs, cum = {}, [], 0.0
    tmp = os.path.join(config.DIR_AVATAR, "_full_tmp")
    os.makedirs(tmp, exist_ok=True)
    # 以首段逐页口型视频的实际参数为准，确保页间静帧片段参数完全一致（否则 concat(-c copy) 失败）
    canon = {"w": 960, "h": 1280, "fps": 25, "pix": "yuv420p", "sr": 16000, "ch": 1}
    canon_set = False
    for sl in slides:
        idx = sl.get("idx")
        timeline[str(idx)] = round(cum, 3)
        pg = _ensure_page_avatar(version, idx, sl, img, motion, persona, person)
        if pg:
            if not canon_set:
                p = _probe_av_params(pg)
                if p:
                    canon = p
                canon_set = True
            d = _wav_duration(_resolve_slide_audio(sl.get("audio")) or "")
            segs.append(pg)
            cum += (d or 0.0)
        else:
            # 该页无口型视频（无音频或生成失败）：用静帧片段占位，保证时间线连续
            d = (_wav_duration(_resolve_slide_audio(sl.get("audio")) or "") or 1.0)
            gc = _make_gap_clip(os.path.join(tmp, "gap_%s_%s.mp4" % (version, idx)), d, img,
                                v_w=canon["w"], v_h=canon["h"], v_fps=canon["fps"],
                                v_pix=canon["pix"], a_sr=canon["sr"], a_ch=canon["ch"])
            if gc:
                segs.append(gc)
                cum += d
            else:
                cum += d
        if gap and gap > 0:
            gc = _make_gap_clip(os.path.join(tmp, "pad_%s_%s.mp4" % (version, idx)), gap, img,
                                v_w=canon["w"], v_h=canon["h"], v_fps=canon["fps"],
                                v_pix=canon["pix"], a_sr=canon["sr"], a_ch=canon["ch"])
            if gc:
                segs.append(gc)
                cum += gap
    if len(segs) < 1:
        return {"ok": False, "note": "无可用的逐页讲解音频，无法生成整场视频（请先「生成讲解」并「生成视频」）。"}
    ok = _concat_videos(segs, out_mp4)
    if not ok:
        # 降级：连续音频讲解（至少能完整听完全场，前端据此走音频兜底）
        full_wav, _t, total = _build_full_audio(version, gap=gap)
        return {"ok": True, "video": None,
                "audio": ("/api/audio?name=" + os.path.basename(full_wav)) if full_wav else None,
                "dur": timeline, "total": total,
                "note": "视频拼接失败，已降级为连续音频讲解。"}
    with open(dur_file, "w", encoding="utf-8") as f:
        json.dump({"timeline": timeline, "total": round(cum, 3), "version": version,
                   "created": time.strftime("%Y-%m-%d %H:%M:%S"), "motion": motion},
                  f, ensure_ascii=False, indent=2)
    return {"ok": True, "video": "/api/video?name=" + os.path.basename(out_mp4),
            "dur": timeline, "total": round(cum, 3), "cached": False}


def _job_worker_full_avatar(jid, version, person, persona, motion, gap, force):
    """后台线程：生成整场连续数字分身视频，进度写入 job。"""
    try:
        _job_update(jid, stage="生成整场数字分身视频（逐页口型+表情微动）…", total=len(SLIDES))
        res = _build_full_avatar(version, person=person, persona=persona,
                                 motion=motion, gap=gap, force=force)
        _job_update(jid, stage="完成" if res.get("ok") else "失败", done=True,
                    full_avatar=res,
                    summary=(res.get("note") or ("整场数字分身视频已生成" if res.get("ok") else "生成失败")))
    except Exception as e:
        import traceback
        _job_update(jid, done=True, error=str(e), stage="失败",
                    note=traceback.format_exc()[-400:])


def _handle_demo_avatar_full(self, data):
    """POST：生成（或复用缓存）整场连续数字分身视频；后台任务，返回 job_id。
    命中缓存则同步秒回。前端轮询 /api/progress 取 full_avatar 结果。"""
    try:
        version = data.get("version") or CURRENT_VERSION
        person = data.get("person") or None
        persona = data.get("avatar") or None
        motion = data.get("motion")
        try:
            motion = AVATAR_MOTION_DEFAULT if motion is None else float(motion)
        except Exception:
            motion = AVATAR_MOTION_DEFAULT
        gap = data.get("gap")
        try:
            gap = 0.4 if gap is None else float(gap)
        except Exception:
            gap = 0.4
        force = bool(data.get("force"))
        out_mp4 = _demo_av_full_path(version)
        dur_file = _demo_av_full_dur(version)
        if (not force) and os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 0 and os.path.exists(dur_file):
            with open(dur_file, "r", encoding="utf-8") as f:
                dd = json.load(f)
            self._json({"ok": True, "cached": True, "job": None,
                        "video": "/api/video?name=" + os.path.basename(out_mp4),
                        "dur": dd.get("timeline"), "total": dd.get("total"), "version": version})
            return
        jid = _job_new(total=len(SLIDES))
        t = threading.Thread(target=_job_worker_full_avatar,
                             args=(jid, version, person, persona, motion, gap, force), daemon=True)
        t.start()
        self._json({"ok": True, "job": jid, "version": version,
                    "stage": "已提交，开始生成整场数字分身视频…"})
    except Exception as e:
        self._json({"error": str(e)})


# ================= ⑤-增强：资源管理 / 历史记录 / 导入导出 =================
# 资源操作历史由 config 按「当前激活项目」动态维护，此处不再重绑常量。

def _res_log(action, target, detail=""):
    """记录一次资源管理操作（改名/删除/生成/导入/导出/切换）到历史 JSON。"""
    try:
        recs = []
        if os.path.exists(config.RESOURCE_HISTORY):
            try:
                recs = json.load(open(config.RESOURCE_HISTORY, "r", encoding="utf-8"))
            except Exception:
                recs = []
        recs.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
                     "target": target, "detail": detail})
        recs = recs[-500:]
        with open(config.RESOURCE_HISTORY, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _res_safe(base, name):
    """把文件名规范为 base 目录内的绝对路径，拦截路径穿越。"""
    fp = os.path.abspath(os.path.join(base, os.path.basename(name)))
    if not (fp == base or fp.startswith(base + os.sep)):
        raise ValueError("非法文件名：%s" % name)
    return fp


def _res_list_category(cat):
    if cat == "audio":
        d, exts = config.DIR_NARR, (".wav",)
    elif cat == "video":
        d, exts = config.DIR_AVATAR, (".mp4",)
    elif cat == "avatar":
        # 数字分身形象：portraits 目录 + 根目录肖像图
        d, exts = config.PORTRAITS_DIR, (".png", ".jpg", ".jpeg", ".webp")
    elif cat == "source":
        d, exts = DIR_BACKUP, BACKUP_DECK_EXT
    else:
        return []
    items = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            fp = os.path.join(d, fn)
            if os.path.isfile(fp) and fn.lower().endswith(exts):
                items.append({"name": fn, "size": os.path.getsize(fp),
                              "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(fp)))})
    return items


def _res_list():
    return {
        "audio": _res_list_category("audio"),
        "video": _res_list_category("video"),
        "avatar": _res_list_category("avatar"),
        "source": _res_list_category("source"),
        "versions": [{"id": k, "name": v.get("name"), "created": v.get("created"),
                     "pages": len(v.get("slides", []))} for k, v in (VERSIONS or {}).items()],
        "personas": list_portraits(),
        "history": ([]
                    if not os.path.exists(config.RESOURCE_HISTORY)
                    else json.load(open(config.RESOURCE_HISTORY, "r", encoding="utf-8"))[-200:]),
    }


def _res_save_portraits(lib):
    """把象库写回 portraits.json（与 config.load_portraits 结构一致）。"""
    os.makedirs(config.PORTRAITS_DIR, exist_ok=True)
    out = {"default": lib.get("default"), "personas": {}}
    for pid, meta in (lib.get("personas") or {}).items():
        m = dict(meta)
        m.pop("file_abs", None)
        out["personas"][pid] = m
    with open(config.PORTRAITS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


# ================= 导入进度（上传 + 解压 两阶段，前端轮询）================
_IMPORT_JOBS = {}
_IMPORT_LOCK = threading.Lock()


def _imp_update(jid, **kw):
    with _IMPORT_LOCK:
        j = _IMPORT_JOBS.setdefault(
            jid, {"stage": "", "pct": 0, "done": False, "count": 0, "cur": ""})
        j.update(kw)


def _imp_get(jid):
    with _IMPORT_LOCK:
        return dict(_IMPORT_JOBS.get(jid)
                    or {"stage": "", "pct": 0, "done": False, "count": 0, "cur": ""})


def _handle_upload_zip(self):
    """zip 二进制直传：流式落盘（上传阶段）→ 后台解压（导入阶段）。

    相比老链路（前端 arrayBuffer→btoa→JSON 上传），这里：
      · 请求体就是文件本身，JS 侧不再产出几百 MB 的字符串（WebView2 不再 OOM）；
      · 服务端按 1MB 分块写临时文件，内存恒定，不整包进内存；
      · 解压放进后台线程，前端用 /api/import_progress?job= 轮询真实进度
        （上传中 → 导入中 i/n → 清理临时文件 → 完成）。
    """
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        jid = (qs.get("job") or [str(int(time.time() * 1000))])[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._json({"ok": False, "error": "缺少上传数据"}); return
        os.makedirs(config.DIR_TEST, exist_ok=True)
        tmp_zip = os.path.join(config.DIR_TEST,
                               "myme_import_%s.zip" % time.strftime("%Y%m%d_%H%M%S"))
        _imp_update(jid, stage="上传中", pct=0, done=False, count=0, cur="")
        done = 0
        with open(tmp_zip, "wb") as f:
            while done < length:
                chunk = self.rfile.read(min(1024 * 1024, length - done))
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _imp_update(jid, pct=int(done * 90 / max(1, length)))
        _imp_update(jid, stage="上传完成，准备导入", pct=90)
        threading.Thread(target=_worker_import_zip, args=(jid, tmp_zip), daemon=True).start()
        self._json({"ok": True, "job": jid, "stage": "上传完成，开始导入", "bytes": done})
    except Exception as e:
        self._json({"ok": False, "error": str(e)})


def _handle_kb_upload(self):
    """知识库备份（.mymekb）二进制直传：流式落盘 → 导入还原 kb_data/。

    与上传播放包同理：请求体就是文件本身，服务端按 1MB 分块写临时文件，
    内存恒定；导入走 knowledge_base.import_backup（流式解包，不整文件进内存）。
    默认 merge=True（合并还原，可与现有库叠加）；整库替换可在前端先清空再导入。
    """
    try:
        import knowledge_base
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._json({"ok": False, "error": "缺少上传数据"}); return
        os.makedirs(config.DIR_TEST, exist_ok=True)
        tmp = os.path.join(config.DIR_TEST,
                           "myme_kb_%s%s"
                           % (time.strftime("%Y%m%d_%H%M%S"), config.KB_BACKUP_EXT))
        done = 0
        with open(tmp, "wb") as f:
            while done < length:
                chunk = self.rfile.read(min(1024 * 1024, length - done))
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
        r = knowledge_base.import_backup(tmp, merge=True)
        try:
            os.remove(tmp)
        except Exception:
            pass
        self._json(r)
    except Exception as e:
        self._json({"ok": False, "error": str(e)})


def _worker_import_zip(jid, tmp_zip):
    """后台解压导入：分阶段更新进度（导入中 → 清理临时文件 → 完成）。

    暂时用不到的文件也只是流式拷贝到对应目录，不做解码、不进内存。
    """
    try:
        _imp_update(jid, stage="导入中", pct=90)
        n = _res_import_zip(tmp_zip, progress=lambda i, t, name: _imp_update(
            jid, stage="导入中 %d/%d" % (i, t), cur=name,
            pct=90 + int(i * 7 / max(1, t))))
        _imp_update(jid, stage="清理临时文件", pct=97, cur="")
        try:
            os.remove(tmp_zip)
        except Exception:
            pass
        # 重新载入幻灯片（可能含 slides.json），与 /api/resources import 行为一致
        _load_slides()
        _res_log("import", "restore", "%d 个项目" % n)
        _imp_update(jid, stage="完成", pct=100, done=True, count=n)
    except Exception as e:
        _imp_update(jid, stage="失败：" + str(e), done=True, error=str(e))


def _handle_resources(self, data):
    """资源管理：list / rename / delete / set_default_persona / set_current_version /
    export / import。所有文件操作均经 _res_safe 防穿越。"""
    try:
        act = (data.get("act") or "list").strip()
        if act == "list":
            self._json({"ok": True, **_res_list()}); return
        if act == "rename":
            cat = data.get("cat"); name = data.get("name") or ""; new = (data.get("new") or "").strip()
            if not cat or not name or not new:
                self._json({"ok": False, "error": "缺少参数"}); return
            if cat in ("audio", "video", "avatar", "source"):
                base = {"audio": config.DIR_NARR, "video": config.DIR_AVATAR, "avatar": config.PORTRAITS_DIR, "source": DIR_BACKUP}[cat]
                oldp = _res_safe(base, name); newp = _res_safe(base, new)
                if not os.path.exists(oldp):
                    self._json({"ok": False, "error": "文件不存在：%s" % name}); return
                if os.path.exists(newp):
                    self._json({"ok": False, "error": "目标名已存在：%s" % new}); return
                os.rename(oldp, newp)
                _res_log("rename", "%s/%s → %s" % (cat, name, new))
                self._json({"ok": True})
            elif cat == "version":
                if name not in VERSIONS:
                    self._json({"ok": False, "error": "版本不存在"}); return
                VERSIONS[name]["name"] = new
                for v in SLIDES_META.get("versions", []):
                    if v.get("id") == name:
                        v["name"] = new; break
                _save_slides(); _res_log("rename_version", name, new)
                self._json({"ok": True})
            elif cat == "persona":
                lib = load_portraits()
                if name not in lib["personas"]:
                    self._json({"ok": False, "error": "分身不存在"}); return
                lib["personas"][name]["name"] = new
                _res_save_portraits(lib); _res_log("rename_persona", name, new)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "未知类别"})
            return
        if act == "delete":
            cat = data.get("cat"); name = data.get("name") or ""
            if cat in ("audio", "video", "avatar", "source"):
                base = {"audio": config.DIR_NARR, "video": config.DIR_AVATAR, "avatar": config.PORTRAITS_DIR, "source": DIR_BACKUP}[cat]
                fp = _res_safe(base, name)
                if not os.path.exists(fp):
                    self._json({"ok": False, "error": "文件不存在：%s" % name}); return
                try:
                    os.remove(fp)
                except Exception as e:
                    self._json({"ok": False, "error": "删除失败：%s" % e}); return
                _res_log("delete", "%s/%s" % (cat, name))
                self._json({"ok": True})
            elif cat == "version":
                if name not in VERSIONS:
                    self._json({"ok": False, "error": "版本不存在"}); return
                del VERSIONS[name]
                if name in VERSION_ORDER:
                    VERSION_ORDER.remove(name)
                SLIDES_META["versions"] = [v for v in SLIDES_META.get("versions", []) if v.get("id") != name]
                if CURRENT_VERSION == name and VERSION_ORDER:
                    CURRENT_VERSION = VERSION_ORDER[0]
                    SLIDES_META["currentVersion"] = CURRENT_VERSION
                _save_slides(); _res_log("delete_version", name)
                self._json({"ok": True})
            elif cat == "persona":
                lib = load_portraits()
                if name not in lib["personas"]:
                    self._json({"ok": False, "error": "分身不存在"}); return
                fp = lib["personas"][name].get("file_abs")
                lib["personas"].pop(name, None)
                if lib.get("default") == name:
                    lib["default"] = next(iter(lib["personas"]), None)
                _res_save_portraits(lib)
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
                _res_log("delete_persona", name)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "未知类别"})
            return
        if act == "set_default_persona":
            pid = data.get("id") or ""
            lib = load_portraits()
            if pid and pid in lib["personas"]:
                lib["default"] = pid; _res_save_portraits(lib); _res_log("set_default_persona", pid)
            self._json({"ok": True, **lib})
            return
        if act == "set_current_version":
            vid = data.get("id") or ""
            if vid in VERSIONS:
                _apply_version(vid); _res_log("set_current_version", vid)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "版本不存在"})
            return
        if act == "export":
            cats = data.get("cats") or ["audio", "video", "avatar", "source", "versions", "personas"]
            only_versions = data.get("versions") or None   # 仅导出这些版本（过滤 slides.json）
            videos = data.get("videos") or None            # 仅打包这些视频文件名
            quality = (data.get("quality") or "lossless").strip()
            if quality not in QUALITY_PRESETS:
                quality = "lossless"
            # include_player：把 player.html + manifest.json + 页图一起打进去，
            # 解压即是一个离线便携播放包（安卓 / 统信 / Win 便携端通用）。
            include_player = bool(data.get("include_player"))
            if include_player and "versions" not in cats:
                cats = list(cats) + ["versions"]
            out_zip = os.path.join(config.DIR_TEST, "myme_playback_%s.zip"
                                   % time.strftime("%Y%m%d_%H%M%S"))
            n = _res_export_zip(out_zip, cats, only_versions=only_versions,
                                videos=videos, quality=quality,
                                include_player=include_player)
            _res_log("export", ",".join(cats), "%d 个文件（%s）" % (n, quality))
            try:
                size = os.path.getsize(out_zip)
            except Exception:
                size = 0
            self._json({"ok": True,
                        "zip": "/api/download?name=" + os.path.basename(out_zip),
                        "path": out_zip, "name": os.path.basename(out_zip), "count": n,
                        "size": size, "quality": quality, "ffmpeg": bool(_FFMPEG)})
            return
        if act == "import":
            b64 = data.get("zip") or ""
            if not b64:
                self._json({"ok": False, "error": "缺少 zip 数据"}); return
            tmp_zip = os.path.join(config.DIR_TEST, "myme_import_%s.zip" % time.strftime("%Y%m%d_%H%M%S"))
            with open(tmp_zip, "wb") as f:
                f.write(base64.b64decode(b64))
            n = _res_import_zip(tmp_zip)
            try:
                os.remove(tmp_zip)
            except Exception:
                pass
            # 重新载入幻灯片（可能含 slides.json）
            _load_slides()
            _res_log("import", "restore", "%d 个项目" % n)
            self._json({"ok": True, "count": n})
            return
        self._json({"ok": False, "error": "未知操作：%s" % act})
    except Exception as e:
        self._json({"ok": False, "error": str(e)})


# ================= 导出质量档位（可选 ffmpeg 转码压缩）================
# lossless：原样拷贝（体积最大、零损耗）
# standard：音频 wav→mp3 192k、视频 x264 crf 23（听感/观感基本无损）
# compact ：音频 mp3 128k、视频 crf 27 + 720p（体积最小，便于微信/邮件传送）
# 未装 ffmpeg 时自动回落为无损原样拷贝，功能不受影响。
QUALITY_PRESETS = {
    "lossless": {"audio": None, "video": None, "label": "无损（原样拷贝）"},
    "standard": {"audio": {"bitrate": "192k"}, "video": {"crf": "23", "scale": None},
                 "label": "标准（mp3 192k / x264 CRF23）"},
    "compact": {"audio": {"bitrate": "128k"}, "video": {"crf": "27", "scale": 720},
                "label": "精简（mp3 128k / 720p CRF27）"},
}

_FFMPEG = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _ff_transcode(src, kind, preset):
    """ffmpeg 转码到临时文件，返回临时路径；不可用/失败返回 None（调用方回落原样）。"""
    if not _FFMPEG or not preset:
        return None
    try:
        fd, tmp = tempfile.mkstemp(suffix=(".mp3" if kind == "audio" else ".mp4"))
        os.close(fd)
        if kind == "audio":
            cmd = [_FFMPEG, "-y", "-i", src, "-vn",
                   "-b:a", str(preset.get("bitrate", "192k")), tmp]
        else:
            cmd = [_FFMPEG, "-y", "-i", src, "-c:v", "libx264",
                   "-crf", str(preset.get("crf", 23)), "-preset", "veryfast",
                   "-c:a", "aac", "-b:a", "128k"]
            if preset.get("scale"):
                cmd += ["-vf", "scale=-2:%d" % int(preset["scale"])]
            cmd += [tmp]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            return tmp
        try:
            os.remove(tmp)
        except Exception:
            pass
        return None
    except Exception:
        return None


def _zadd(z, arcname, fp, quality):
    """按质量档位写入 zip：需要压缩且 ffmpeg 可用则转码后写入，否则原样拷贝。

    转码后的扩展名会变为 .mp3 / .mp4；导入侧按目录前缀（audio/ video/）归位，
    因此兼容无损导出的包，播放端也能正常播放 mp3。
    """
    preset = QUALITY_PRESETS.get(quality) or QUALITY_PRESETS["lossless"]
    low = (fp or "").lower()
    kind = None
    if preset.get("audio") and low.endswith((".wav", ".flac", ".aiff", ".aif")):
        kind = "audio"
    elif preset.get("video") and low.endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
        kind = "video"
    if kind:
        tmp = _ff_transcode(fp, kind, preset[kind])
        if tmp:
            try:
                arc = os.path.splitext(arcname)[0] + (".mp3" if kind == "audio" else ".mp4")
                z.write(tmp, arc)
                return
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    z.write(fp, arcname)


def _rewrite_audio_ref(path, quality):
    """压缩档位下把 slides.json 里的音频引用 .wav 改写为 .mp3。

    导出时音频已转码为 mp3，若讲稿仍指向 .wav，播放端就找不到音频（整场静音）。
    """
    if quality == "lossless":
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
        new = txt.replace(".wav", ".mp3")
        if new != txt:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
    except Exception:
        pass


def _zadd_slides(z, src, quality, out_zip):
    """写 slides.json 到 zip（压缩档位下同步改写音频引用后缀，保证转码后仍能播）。"""
    if quality == "lossless":
        z.write(src, "slides.json")
        return
    tmp = out_zip + ".slides.json"
    try:
        with open(src, "r", encoding="utf-8") as f:
            txt = f.read()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt.replace(".wav", ".mp3"))
        z.write(tmp, "slides.json")
    except Exception:
        z.write(src, "slides.json")
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _basename_of_url(u):
    """/api/audio?name=narr-01.wav -> narr-01.wav（也容忍直接就是文件名）。"""
    s = str(u or "")
    return os.path.basename(s.split("?name=")[-1])


def _find_file(name, dirs):
    """在若干目录（含子目录）里找同名文件，返回绝对路径或 ''。"""
    if not name:
        return ""
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _ds, files in os.walk(d):
            if name in files:
                return os.path.join(root, name)
    return ""


def _build_manifest(slides_doc, only_versions, quality, packed_videos):
    """生成便携播放包的 manifest.json：把 /api/xxx?name= 的引用改写成包内相对路径。

    播放端（安卓 APK / 统信 deb / Win 便携包里的 player.html）只读这一个文件，
    不必理解主程序的 slides.json 结构，也不依赖任何服务端接口。
    新增（v2.6.1 反馈）：audio_en（英配）、personas（可选分身列表）、
    videoAlt（各分身逐页视频变体，供播放端"选分身"切换）。
    """
    wav2mp3 = (quality != "lossless")
    vs = slides_doc.get("versions") or {}
    if only_versions:
        vs = {k: v for k, v in vs.items() if k in set(only_versions)}
    # 可选项：分身列表（默认分身 + 各形象），播放端据此渲染"选分身"下拉
    personas = []
    try:
        lib = list_portraits()
        personas.append({"id": "default",
                         "name": (lib.get("default") or {}).get("name") or "默认分身"})
        for pid, meta in (lib.get("personas") or {}).items():
            personas.append({"id": pid, "name": (meta or {}).get("name") or pid})
    except Exception:
        personas = []
    out = []
    for vid, v in vs.items():
        raw = (v or {}).get("slides") or []
        pages = []
        vs_id = "".join(c if c.isalnum() else "_" for c in str(vid))
        for i, s in enumerate(raw, 1):
            idx = s.get("idx", i)
            img = _basename_of_url(s.get("img"))
            if not img:
                imgs = s.get("images") or []
                if imgs:
                    img = _basename_of_url(imgs[0])
            au = _basename_of_url(s.get("audio"))
            if au and wav2mp3 and au.lower().endswith(".wav"):
                au = au[:-4] + ".mp3"
            aue = _basename_of_url(s.get("audio_en"))
            if aue and wav2mp3 and aue.lower().endswith(".wav"):
                aue = aue[:-4] + ".mp3"
            # 逐页分身视频：demo_av_<版本>_<页码>.mp4（默认分身）
            vname = "demo_av_%s_%d.mp4" % (vid, idx)
            # 各分身变体：demo_av_<版本>_<分身>_<页码>.mp4
            video_alt = {}
            pat = re.compile(r"^demo_av_%s_(.+?)_%d\.mp4$" % (re.escape(vs_id), idx))
            for fn in packed_videos:
                m = pat.match(fn)
                if m:
                    video_alt[m.group(1)] = "video/" + fn
            pages.append({
                "idx": idx,
                "title": s.get("title", ""),
                "text": s.get("text", ""),
                "notes": s.get("notes", ""),
                "img": ("images/" + img) if img else "",
                "audio": ("audio/" + au) if au else "",
                "audio_en": ("audio/" + aue) if aue else "",
                "video": ("video/" + vname) if vname in packed_videos else "",
                "videoAlt": video_alt,
            })
        full = ""
        dur = ""
        fn = "demo_av_full_%s.mp4" % vid
        if fn in packed_videos:
            full = "video/" + fn
            dn = "demo_av_full_%s_dur.json" % vid
            if os.path.isfile(os.path.join(config.DIR_AVATAR, dn)):
                dur = "video/" + dn
        out.append({"id": vid, "name": (v or {}).get("name") or vid,
                    "created": (v or {}).get("created", ""),
                    "slides": pages,
                    "full": {"video": full, "dur": dur}})
    return {"app": "myme", "manifest": 1, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "personas": personas, "versions": out}


def _res_export_zip(out_zip, cats, only_versions=None, videos=None, quality="lossless",
                    include_player=False):
    """把选定类别导出为一个 zip（slides.json + 各资源目录）。返回文件数。

    only_versions: 若给定（版本 id 列表），slides.json 仅保留这些版本。
    videos:        若给定（视频文件名列表），video 目录仅打包这些文件；
    include_player: True 时额外写入 player.html + manifest.json，
                    解压后就是一个「离线便携播放包」，可直接塞进安卓 APK / 统信 deb / Win 便携包。
                   否则按 only_versions 收敛（导出所选版本的整场/逐页分身视频），
                   再否则整目录打包。
    quality:       lossless / standard / compact（见 QUALITY_PRESETS）；
                   音视频按档位用 ffmpeg 转码压缩，无 ffmpeg 时自动原样拷贝。
    """
    cnt = 0
    packed_videos = set()
    slides_doc = {}
    _added = set()

    def _final_arc(arc):
        """压缩档位下 .wav 会被转码成 .mp3，包内最终名字随之改变（用于去重）。"""
        if quality != "lossless" and arc.lower().endswith(".wav"):
            return arc[:-4] + ".mp3"
        return arc

    def _put(arc, fp):
        """按最终名字去重写入：narr-01.wav 与 narr-01.mp3 转码后同名，只留一份。"""
        nonlocal cnt
        a2 = _final_arc(arc)
        if a2 in _added:
            return
        _added.add(a2)
        _zadd(z, arc, fp, quality)
        cnt += 1
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        # 方案（版本）：整份 slides.json 即含全部版本讲稿；可按 only_versions 过滤
        if "versions" in cats and os.path.exists(config.SLIDES_FILE):
            try:
                slides_doc = json.load(open(config.SLIDES_FILE, encoding="utf-8")) or {}
                vsd = slides_doc.get("versions") or {}
                if only_versions:
                    slides_doc["versions"] = {k: v for k, v in vsd.items() if k in set(only_versions)}
            except Exception:
                slides_doc = {}
            if only_versions:
                try:
                    d = json.load(open(config.SLIDES_FILE, encoding="utf-8"))
                    vs = d.get("versions") or {}
                    keep = set(only_versions)
                    d["versions"] = {k: v for k, v in vs.items() if k in keep}
                    # 收敛 currentVersion 到所选（不在则取第一个）
                    if isinstance(d.get("meta"), dict):
                        if d["meta"].get("currentVersion") not in d["versions"]:
                            d["meta"]["currentVersion"] = next(iter(d["versions"]), None)
                    tmp = out_zip + ".slides.json"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False)
                    _rewrite_audio_ref(tmp, quality)
                    z.write(tmp, "slides.json"); cnt += 1
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                except Exception:
                    _zadd_slides(z, config.SLIDES_FILE, quality, out_zip); cnt += 1
            else:
                _zadd_slides(z, config.SLIDES_FILE, quality, out_zip); cnt += 1
        # 个人形象：portraits.json + portraits 目录
        if "personas" in cats:
            if os.path.exists(config.PORTRAITS_JSON):
                z.write(config.PORTRAITS_JSON, "portraits/portraits.json"); cnt += 1
            if os.path.isdir(config.PORTRAITS_DIR):
                for fn in os.listdir(config.PORTRAITS_DIR):
                    fp = os.path.join(config.PORTRAITS_DIR, fn)
                    if os.path.isfile(fp) and fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        z.write(fp, "portraits/" + fn); cnt += 1
        # 音频 / 视频 / 数字分身 / 源文件
        for cat, d in (("audio", config.DIR_NARR), ("video", config.DIR_AVATAR), ("source", DIR_BACKUP)):
            if cat not in cats or not os.path.isdir(d):
                continue
            if cat == "video":
                pick = set(videos) if videos else None
                include = set()
                for fn in os.listdir(d):
                    if not fn.lower().endswith(".mp4"):
                        continue
                    base = fn[:-4]
                    if pick is not None:
                        if fn in pick:
                            include.add(fn)
                        continue
                    if only_versions and "versions" in cats:
                        for v in only_versions:
                            if base == "demo_av_full_%s" % v or base.startswith("demo_av_%s_" % v):
                                include.add(fn); break
                    else:
                        include.add(fn)
                for fn in include:
                    fp = os.path.join(d, fn)
                    if os.path.isfile(fp):
                        _put("video/" + fn, fp)
                        packed_videos.add(fn)
                # 逐页时间轴(demo_av_full_*_dur.json) 跟随 included 的整场视频导出，
                # 否则播放版拿到整场视频却无法按页同步翻页。
                for fn in os.listdir(d):
                    if fn.endswith("_dur.json"):
                        vname = fn[:-len("_dur.json")]
                        if (vname + ".mp4") in include:
                            fp = os.path.join(d, fn)
                            if os.path.isfile(fp):
                                z.write(fp, "video/" + fn); cnt += 1
                continue
            # 音频 / 源文件：整目录打包（音视频按质量档位压缩）
            # 只导出所选版本时，音频按「讲稿实际引用 + full_<版本>.*」收敛，
            # 否则会把其它版本的录音一并打包，几百 MB 白白撑大播放包。
            keep = None
            if cat == "audio" and only_versions and "versions" in cats:
                keep = set()
                for _vid in only_versions:
                    _v = (slides_doc.get("versions") or {}).get(_vid) or {}
                    for s in (_v.get("slides") or []):
                        b = _basename_of_url(s.get("audio"))
                        if b:
                            stem = os.path.splitext(b)[0]
                            keep.add(b); keep.add(stem + ".wav"); keep.add(stem + ".mp3")
                    for fn in os.listdir(d):
                        if fn.startswith("full_%s." % _vid) or fn.startswith("full_%s_" % _vid):
                            keep.add(fn)
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                if os.path.isfile(fp):
                    if keep is not None and fn not in keep:
                        continue
                    _put("%s/%s" % (cat, fn), fp)
        # 知识库（含讲稿入库文本）一并备份
        if "kb" in cats and os.path.isdir(config.DIR_KB):
            for fn in os.listdir(config.DIR_KB):
                fp = os.path.join(config.DIR_KB, fn)
                if os.path.isfile(fp):
                    z.write(fp, "kb_data/" + fn); cnt += 1

        # ---- 便携播放包：页图 + 离线播放器 + manifest（安卓 / 统信 / Win 便携端直接吃）----
        if include_player or "images" in cats:
            refs = []
            for _v in (slides_doc.get("versions") or {}).values():
                for s in ((_v or {}).get("slides") or []):
                    for u in ([s.get("img")] + list(s.get("images") or [])):
                        b = _basename_of_url(u)
                        if b:
                            refs.append(b)
            seen = set()
            # 页图可能落在：当前项目 test_out / 知识库 / 源码树 test_out / slide_img（历史渲染批次）
            _img_dirs = (config.DIR_TEST, config.DIR_KB,
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_out"),
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "slide_img"))
            for b in refs:
                if b in seen:
                    continue
                fp = _find_file(b, _img_dirs)
                if fp:
                    seen.add(b)
                    z.write(fp, "images/" + b); cnt += 1
        if include_player:
            try:
                man = _build_manifest(slides_doc, only_versions, quality, packed_videos)
                z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=1))
                cnt += 1
            except Exception as e:
                print("[export] manifest 生成失败：", e)
            psrc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "player.html")
            if os.path.isfile(psrc):
                z.write(psrc, "player.html"); cnt += 1
            # 播放包说明（用户解压后第一眼看到）
            # ⚠ 文件名必须 ASCII：aapt2 打安卓 assets 时非 ASCII 名会 "failed to open file"，
            #    为了让同一个播放包能直接进 APK，这里统一用 README.txt（内容仍是中文）。
            z.writestr("README.txt", PLAYBACK_README)
            cnt += 1
    return cnt


PLAYBACK_README = """myme 便携播放包
================

【最简单的播放方式】
  双击 player.html（用 Chrome / Edge / 火狐打开即可），点 ▶ 就开始播。
  全程离线，不需要安装任何软件，也不需要联网。

【手机上播放】
  把整个目录拷到手机，用手机浏览器打开 player.html；
  或安装配套的 myme 安卓播放版 APK（内置同一个播放器，点图标即可）。

【统信 UOS 上播放】
  安装 myme 播放版 deb 后，桌面/开始菜单点「myme 播放版」；
  本质是启动一个本地服务再用浏览器打开同一个播放器，无需额外依赖。

【目录里都是什么】
  manifest.json  播放清单（版本 / 每页文字 / 配音 / 视频 / 页图）
  player.html    离线播放器（纯静态，零依赖）
  slides.json    完整讲稿（导入回完整版时用得上）
  images/        幻灯片页图与内嵌配图
  audio/         每页配音
  video/         数字分身视频（整场 + 逐页）
  portraits/     分身形象图

【快捷键】
  → 下一页    ← 上一页    空格 播放/暂停    Esc 关闭目录
"""


def _stream_copy(src, dst, chunk=1024 * 1024):
    """分块流式拷贝：任何大小的文件都只占固定内存（1MB），绝不整文件进内存。

    用于替换 dst.write(src.read()) —— 后者对 112MB 视频会一次性吃 112MB 内存，
    几百 MB 的播放包叠加起来就是 WebView2 / 服务端 OOM 的元凶之一。
    """
    try:
        while True:
            buf = src.read(chunk)
            if not buf:
                break
            dst.write(buf)
    finally:
        try:
            dst.close()
        except Exception:
            pass
        try:
            src.close()
        except Exception:
            pass


def _res_import_zip(zip_path, progress=None):
    """从导出 zip 恢复资源到对应目录（slides.json 覆盖当前，其余按目录归位）。

    全程流式：每个条目按 1MB 分块落地，不把大音视频整体读进内存。
    暂时用不到的文件也只是原样拷贝到对应目录，不做任何解码/加载。
    progress: 可选回调 (idx, total, 文件名)，用于前端进度条。
    """
    cnt = 0
    with zipfile.ZipFile(zip_path, "r") as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        total = len(infos)
        for idx, info in enumerate(infos, 1):
            name = info.filename
            if progress:
                try:
                    progress(idx, total, os.path.basename(name))
                except Exception:
                    pass
            if name == "slides.json":
                # 归位到「当前项目」根（slides.json 属项目作用域，不再落到 DATA_DIR 顶层）
                os.makedirs(os.path.dirname(config.SLIDES_FILE), exist_ok=True)
                with open(os.path.join(os.path.dirname(config.SLIDES_FILE),
                                       "slides.json"), "wb") as dst:
                    _stream_copy(z.open(name), dst)
                cnt += 1
                continue
            # 归位到对应数据目录
            target = None
            if name.startswith("portraits/"):
                target = os.path.join(config.PORTRAITS_DIR, os.path.basename(name))
            elif name.startswith("audio/"):
                target = os.path.join(config.DIR_NARR, os.path.basename(name))
            elif name.startswith("video/"):
                target = os.path.join(config.DIR_AVATAR, os.path.basename(name))
            elif name.startswith("source/"):
                target = os.path.join(DIR_BACKUP, os.path.basename(name))
            elif name.startswith("kb_data/"):
                target = os.path.join(config.DIR_KB, os.path.basename(name))
            elif name.startswith("images/"):
                # ⚠ 关键修复（#381）：便携播放包导入时，images/ 之前被静默丢弃，
                #    导致在「干净的其他 Win 电脑」上用播放版导入后只显文字不显页图
                #    （本机因创作时已渲染过同名页图留在 DIR_TEST 而侥幸正常）。
                #    这里把页图恢复到 DIR_TEST，/api/img 才能按名找回。
                target = os.path.join(config.DIR_TEST, os.path.basename(name))
            if target:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as dst:
                    _stream_copy(z.open(name), dst)
                cnt += 1
    return cnt


# ================= 制作 PPT（大模型生成大纲 + 可选 ComfyUI 配图）================
def _comfyui_gen_image(prompt, seed=0):
    """可选：调本机 ComfyUI 文生图，返回本地图片绝对路径；任何失败返回 None（不影响主流程）。"""
    if not config.COMFYUI_IMG_ENABLED or not config.COMFYUI_TXT2IMG_CKPT:
        return None
    try:
        import uuid
        url = config.COMFYUI_API_URL.rstrip("/")
        ckpt = config.COMFYUI_TXT2IMG_CKPT
        wf = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": 20, "cfg": 7.0, "sampler_name": "euler",
                "scheduler": "normal", "denoise": 1.0,
                "model": {"0": ["9", "0"]}, "positive": {"0": ["6", "0"]},
                "negative": {"0": ["7", "0"]}, "latent_image": {"0": ["5", "0"]}}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": {"0": ["9", "1"]}}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry, distorted", "clip": {"0": ["9", "1"]}}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": {"0": ["3", "0"]}, "vae": {"0": ["9", "2"]}}},
            "9": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "10": {"class_type": "SaveImage", "inputs": {"images": {"0": ["8", "0"]}}},
        }
        if config.COMFYUI_TXT2IMG_LORA:
            wf["11"] = {"class_type": "LoraLoader", "inputs": {
                "lora_name": config.COMFYUI_TXT2IMG_LORA, "strength_model": 0.8, "strength_clip": 0.8,
                "model": {"0": ["9", "0"]}, "clip": {"0": ["9", "1"]}}}
            wf["3"]["inputs"]["model"] = {"0": ["11", "0"]}
            wf["6"]["inputs"]["clip"] = {"0": ["11", "1"]}
            wf["7"]["inputs"]["clip"] = {"0": ["11", "1"]}
        # 提交任务
        req = urllib.request.Request(url + "/prompt",
                                    data=json.dumps({"prompt": wf, "client_id": str(uuid.uuid4())}.encode("utf-8"),),
                                    headers={"Content-Type": "application/json"})
        pid = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8")).get("prompt_id")
        if not pid:
            return None
        # 轮询结果（最多 ~120s）
        for _ in range(60):
            his = json.loads(urllib.request.urlopen(url + "/history", timeout=10).read().decode("utf-8"))
            if pid in his:
                outs = his[pid].get("outputs", {})
                for o in outs.values():
                    for img in o.get("images", []):
                        fn = img.get("filename")
                        # 优先取 ComfyUI 输出目录；取不到则交由上层忽略
                        for d in (os.path.join(config.COMFYUI_ROOT, "output"),):
                            fp = os.path.join(d, fn)
                            if os.path.exists(fp):
                                dst = os.path.join(config.DIR_TEST, "pptimg_%d_%s" % (seed, os.path.basename(fn)))
                                shutil.copy(fp, dst)
                                return dst
                return None
            time.sleep(2)
    except Exception as e:
        print("[make_ppt] ComfyUI 配图失败（跳过，PPT 仍按文字生成）:", e)
    return None


class _SafeStream:
    """绝不抛异常的 stdout/stderr 包装。

    ⚠ 真踩过的坑：把 exe 的输出重定向到文件/管道时，Windows 下 Python 会用 ANSI
      代码页（cp1252）包一层 TextIOWrapper，任何含中文的 print 都会抛
      UnicodeEncodeError 并把整个进程带走（表现为"双击没反应"，错误只在
      myme_error.log 里）。reconfigure 不一定生效（windowed 模式下流可能不是
      标准 TextIOWrapper），所以这里直接把 write 包成"失败就丢"，日志绝不致命。
    """
    def __init__(self, s):
        self._s = s

    def write(self, b):
        try:
            self._s.write(b)
            self._s.flush()
        except Exception:
            pass
        return len(b) if isinstance(b, str) else 0

    def flush(self):
        try:
            self._s.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def __getattr__(self, n):
        return getattr(self._s, n)


def run(port=PORT):
    # 输出流做成"永不致命"：编码不对、管道断了都不能拖垮主服务
    for _nm in ("stdout", "stderr"):
        _s = getattr(sys, _nm, None)
        if _s is not None and not isinstance(_s, _SafeStream):
            try:
                setattr(sys, _nm, _SafeStream(_s))
            except Exception:
                pass
    _ensure_pwa_assets()
    _start_tts_service()
    _start_avatar_service()
    # 子服务保活：TTS / 数字人偶尔自行退出，由守护线程自动拉起，避免演示中途失声
    threading.Thread(target=_child_watchdog, args=(30,), daemon=True).start()
    # 必须是多线程：PPT 讲解等长任务由后台线程执行，单线程会让整个 UI 被阻塞（表现为"点了没反应"）。
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PPT制作及分身演示已启动：http://127.0.0.1:{port}  （Ctrl+C 退出）")
    print(f"PWA 已启用（manifest + service worker），可浏览器'添加到主屏'。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
