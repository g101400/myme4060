# -*- coding: utf-8 -*-
"""
myme.make_ppt — 全自动 PPT 生成的大纲层

能力：
  1) analyze_need()  —— 需求解析：判断描述是否够用，不够就给出追问与建议参数（离线规则兜底）
  2) kb_context()    —— 检索本地知识库，取出与主题最相关的事实/数据/案例
  3) gen_outline()   —— 生成结构化大纲：每页含 标题 / 要点 / 版式 / 图标关键词 / 图表 / 依据

输出 slide 结构：
  {"title":str, "bullets":[str], "layout":"cover|bullets|compare|flow|data",
   "icons":[str], "chart":{"type":"none|bar|line|pie","unit":str,
   "labels":[str],"values":[num]}, "source":str}

大模型接入由「设置 → 大模型接入」统一配置（llm_client），无需硬编码地址/密钥。
大模型不可用时会退化为规则骨架，保证「离线也能出 PPT」。
"""
import json
import re
import config
import llm_client

VALID_LAYOUTS = ("cover", "bullets", "compare", "flow", "data")
VALID_CHARTS = ("none", "bar", "line", "pie")


# ============================ ① 需求解析与追问 ============================
def analyze_need(topic, audience="", scene="", industry="", pages=0, style="商务简洁"):
    """解析用户输入是否足够开工。

    返回 {enough, missing, questions, suggest, level}
      enough    : 是否可以直接生成
      missing   : 缺失维度（受众 / 场景 / 页数 / 行业）
      questions : 建议追问的问题（可直接展示给用户）
      suggest   : 智能补全的建议参数（用户可一键采用）
      level     : 'vague' 太模糊 / 'partial' 部分 / 'ok' 充分
    """
    topic = (topic or "").strip()
    audience = (audience or "").strip()
    scene = (scene or "").strip()
    industry = (industry or "").strip()
    try:
        pages = int(pages or 0)
    except Exception:
        pages = 0

    missing, questions = [], []
    # 主题是否太模糊：过短、或只是泛泛名词
    vague = len(topic) < 6
    if not topic:
        missing.append("主题")
        questions.append("这次汇报的核心主题是什么？（例如：水利一张图 APP 推广方案）")
    elif vague:
        missing.append("主题（过于简略）")
        questions.append("能把主题说得更具体些吗？比如面向什么业务、要解决什么问题。")

    if not audience:
        missing.append("受众")
        questions.append("主要讲给谁听？（领导 / 客户 / 技术同事 / 学生 …）")
    if not scene:
        missing.append("汇报场景")
        questions.append("用在什么场合？（立项汇报 / 阶段汇报 / 培训授课 / 产品发布 …）")
    if not pages:
        missing.append("页数")
        questions.append("大概需要几页？（常用 8 / 12 / 20 页）")
    if not industry:
        missing.append("行业")
        questions.append("属于哪个行业/领域？（便于用词与案例更贴合）")

    # 建议参数（智能补全）：主题越正式页数越多；培训/发布场景偏多页
    if not pages:
        pages_sug = 12 if scene in ("培训授课", "产品发布", "立项汇报") else 8
    else:
        pages_sug = max(3, min(pages, 40))
    suggest = {
        "audience": audience or "单位领导与业务同事",
        "scene": scene or "工作汇报",
        "industry": industry or "（未指定，按通用商务处理）",
        "pages": pages_sug,
        "style": style or "商务简洁",
    }
    # 风格建议：按场景
    if scene in ("产品发布", "立项汇报"):
        suggest["style"] = "科技感/正式"
    elif scene == "培训授课":
        suggest["style"] = "教学清晰"

    level = "vague" if (vague or len(missing) >= 3) else ("ok" if not missing else "partial")
    return {
        "enough": level != "vague",
        "missing": missing,
        "questions": questions,
        "suggest": suggest,
        "level": level,
        "topic": topic,
    }


# ============================ ② 本地知识库检索 ============================
def kb_context(topic, kb_name="myme_kb", top_k=6, compress=True):
    """取与主题最相关的知识库片段。返回 (文本, 命中条数)；任何异常返回 ("",0)。

    compress=True 时（默认）走「调用层上下文压缩」：只把最相关句子/要点送给生成模型，
    减少 token、抑制噪声；检索无命中或压缩失败时回落全量 format_context。
    """
    try:
        import knowledge_base
        res = knowledge_base.hybrid_query(topic, name=kb_name, top_k=top_k)
        if not res:
            return "", 0
        if compress and getattr(config, "KB_COMPRESS", True):
            try:
                txt, _ = knowledge_base.compress_context(
                    topic, res, max_tokens=getattr(config, "KB_COMPRESS_TOKENS", 1200))
                if txt:
                    return txt, len(res)
            except Exception as e:
                print("[make_ppt] 上下文压缩失败，回退全量:", e)
        try:
            ctx = knowledge_base.format_context(res)
        except Exception:
            ctx = "\n".join(str(r.get("text", "")) for r in res)
        return ctx or "", len(res)
    except Exception as e:
        print("[make_ppt] 知识库检索不可用:", e)
        return "", 0


# ============================ ③ 大纲生成 ============================
_SYSTEM = (
    "你是一位专业的 PPT 大纲与版式设计助手。根据用户主题、受众、场景、页数，"
    "并结合提供的【知识库素材】，输出结构清晰的演示文稿大纲。只输出 JSON，不要任何解释或代码块标记。"
)

_PROMPT_HEADER = (
    "请为以下需求撰写演示文稿大纲：\n"
    "主题：{topic}\n"
    "受众：{audience}\n"
    "场景：{scene}\n"
    "行业：{industry}\n"
    "风格：{style}\n"
    "语言：{lang}\n"
    "页数（含封面与结尾）：约 {pages} 页\n"
    "\n【知识库素材】（有则优先引用其中的事实、数据、案例；引用处把出处写进 source 字段）：\n"
    "{kb}\n"
    "\n要求：\n"
    "1. 第 1 页为封面：layout=cover，title 为演示标题，bullets 为 2~4 个核心要点。\n"
    "2. 最后 1 页为总结：title 含「总结」或「结尾」，bullets 为结论与下一步。\n"
    "3. 每页 bullets 3~6 条，每条不超过 28 字；语言简洁专业。\n"
    "4. layout 取值：cover（封面）/ bullets（要点）/ compare（对比）/ flow（流程）/ data（数据页）。"
    "正文页按内容选最合适的一种。\n"
    "5. icons：给 1~3 个图标关键词（如 增长 / 流程 / 用户 / 成本 / 风险 / 协同），仅关键词，不要描述。\n"
    "6. chart：只有当知识库或常识中确有名确数值时才给图表（type 取 bar/line/pie，配 labels 与 values）；"
    "没有可靠数据就写 type=none，绝不编造数字。\n"
    "7. 知识库没有依据、但需要呈现的内容，在 bullets 末尾加【待补充】标注，不要编造事实。\n"
    "\n严格按如下 JSON 数组格式输出（不要 Markdown 代码块，不要多余文字）：\n"
)
# JSON 示例用纯字符串拼接，避免与上方 .format 的占位符冲突（踩坑 #24）
_PROMPT_JSON_EXAMPLE = (
    '[{"title":"封面标题","bullets":["要点1","要点2"],"layout":"cover",'
    '"icons":["增长"],"chart":{"type":"none","unit":"","labels":[],"values":[]},"source":""},'
    '{"title":"现状与数据","bullets":["要点1","要点2"],"layout":"data",'
    '"icons":["成本","效率"],"chart":{"type":"bar","unit":"万元","labels":["2023","2024"],'
    '"values":[120,180]},"source":"知识库：xx报告"}]'
)


def _build_prompt(topic, audience, scene, industry, style, lang, pages, kb):
    return _PROMPT_HEADER.format(topic=topic, audience=audience, scene=scene,
                                 industry=industry, style=style, lang=lang,
                                 pages=pages, kb=kb or "（本次未使用知识库）") \
        + _PROMPT_JSON_EXAMPLE


def _norm_slide(it):
    """规范化一页：补全/校验 layout、icons、chart，过滤空内容。"""
    if not isinstance(it, dict):
        return None
    title = str(it.get("title") or "").strip()
    if not title:
        return None
    bullets = it.get("bullets") or it.get("points") or []
    bullets = [str(b).strip() for b in bullets if str(b).strip()]

    layout = str(it.get("layout") or "").strip().lower()
    if layout not in VALID_LAYOUTS:
        # 按内容猜一个版式，保证渲染有变化
        low = title + "".join(bullets)
        if "对比" in low or "vs" in low.lower():
            layout = "compare"
        elif "流程" in low or "步骤" in low:
            layout = "flow"
        elif "数据" in low or "增长" in low or "%" in low:
            layout = "data"
        else:
            layout = "bullets"

    icons = it.get("icons") or []
    icons = [str(x).strip() for x in icons if str(x).strip()][:3]

    chart = {"type": "none", "unit": "", "labels": [], "values": []}
    c = it.get("chart") or {}
    if isinstance(c, dict):
        ctype = str(c.get("type") or "none").strip().lower()
        if ctype in VALID_CHARTS:
            labels = [str(x) for x in (c.get("labels") or [])][:8]
            vals = []
            for v in (c.get("values") or [])[:8]:
                try:
                    vals.append(float(v))
                except Exception:
                    vals.append(0.0)
            if ctype != "none" and labels and len(vals) == len(labels) and any(vals):
                chart = {"type": ctype, "unit": str(c.get("unit") or ""),
                         "labels": labels, "values": vals}
    # 有图表就强制数据版式
    if chart["type"] != "none":
        layout = "data"

    return {"title": title, "bullets": bullets, "layout": layout,
            "icons": icons, "chart": chart,
            "source": str(it.get("source") or "").strip()}


def _extract_json(text):
    """从模型输出中稳健提取 JSON 数组（兼容带 ```json 代码块或夹杂文字）。"""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    s, e = t.find("["), t.rfind("]")
    if s != -1 and e != -1 and e > s:
        t = t[s:e + 1]
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if isinstance(obj, dict):
        obj = obj.get("slides") or obj.get("outline") or [obj]
    if not isinstance(obj, list) or not obj:
        return None
    out = []
    for it in obj:
        norm = _norm_slide(it)
        if norm:
            out.append(norm)
    return out or None


def gen_outline(topic, style="商务简洁", lang="中文", pages=6,
                audience="", scene="", industry="", use_kb=True, kb_name="myme_kb"):
    """生成完整大纲（含版式/图标/图表）。

    知识库可用时先检索相关素材并要求模型优先引用、无依据标【待补充】；
    大模型不可用则退化为规则骨架（仍带 layout/icons，保证可渲染）。
    """
    pages = max(3, min(int(pages or 6), 40))
    kb, kb_hits = ("", 0)
    if use_kb:
        kb, kb_hits = kb_context(topic or (industry + " " + scene), kb_name=kb_name)

    prompt = _build_prompt(topic or "演示文稿", audience or "单位领导与业务同事",
                           scene or "工作汇报", industry or "通用",
                           style or "商务简洁", lang or "中文", pages, kb)
    try:
        raw = llm_client.chat(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=2400)
        out = _extract_json(raw)
        if out and 1 <= len(out) <= 40:
            return out, {"source": "llm", "kb_hits": kb_hits}
    except Exception as e:
        print("[make_ppt] 大模型生成失败，使用骨架兜底:", e)

    # 兜底骨架：封面 + 内容页 + 总结（离线也能出 PPT）
    n = max(3, min(pages, 20))
    skeleton = [{"title": topic or "演示文稿",
                 "bullets": ["背景与目标", "核心内容", "预期成果", "【待补充】关键数据"],
                 "layout": "cover", "icons": ["目标"],
                 "chart": {"type": "none", "unit": "", "labels": [], "values": []},
                 "source": ""}]
    layouts = ["bullets", "flow", "compare", "data"]
    for i in range(1, n - 1):
        skeleton.append({"title": "第%d部分" % i,
                         "bullets": ["要点一【待补充】", "要点二【待补充】", "要点三【待补充】"],
                         "layout": layouts[(i - 1) % len(layouts)],
                         "icons": ["流程"] if layouts[(i - 1) % len(layouts)] == "flow" else ["要点"],
                         "chart": {"type": "none", "unit": "", "labels": [], "values": []},
                         "source": ""})
    skeleton.append({"title": "总结与展望", "bullets": ["主要结论", "下一步计划"],
                     "layout": "bullets", "icons": ["总结"],
                     "chart": {"type": "none", "unit": "", "labels": [], "values": []},
                     "source": ""})
    return skeleton[:n], {"source": "skeleton", "kb_hits": kb_hits}
