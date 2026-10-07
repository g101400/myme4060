# -*- coding: utf-8 -*-
"""domain_water —— 领域包（水利古建 · 感知应用 · 三端四平台）

把「行业口径」做成可编辑配置，而不是写死在提示词里：术语表、PPT 骨架、
导入模板、问答口径都能在界面改，改完落盘 KP_ROOT/domains/water.json。

三个内置档：
    water   水利一张图 / 工程运行（三端四平台）
    heritage水利古建遗产（木构 · 砖石 · 堤防 · 碑刻 · 彩画 · 修缮）
    sensor  感知应用（雨量 · 水位 · 流量 · 闸门 · 泵站 · 视频AI · 物联网）

对外：
    domain_water.list_profiles()
    domain_water.get("water")                  # 完整配置
    domain_water.outline("water", "汇报主题")  # 领域化 PPT 骨架
    domain_water.kb_entry("water")             # 领域说明文本（可直接喂知识库）
    domain_water.update("water", {"tri_ends": [...]})
"""

import os
import re
import json
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config

DIR_DOMAIN = os.path.join(config.KP_ROOT, "domains")
os.makedirs(DIR_DOMAIN, exist_ok=True)
FILE_WATER = os.path.join(DIR_DOMAIN, "water.json")

# ==================================================================
# 内置默认口径（界面可改；这里给的是常见口径，不是唯一口径）
# ==================================================================
DEFAULT_PROFILES = {
    "water": {
        "name": "水利一张图 · 三端四平台",
        "note": "工程运行与信息化，默认口径为常见做法，可在界面按实际改。",
        "tri_ends": ["管理端（PC 后台 · 业务办理与工单流程）",
                     "移动端（APP / 小程序 · 外业巡查与随手拍）",
                     "大屏端（可视化指挥 · 一张图会商）"],
        "four_platforms": ["监测感知平台（雨量、水位、流量、闸泵状态）",
                           "数据中枢平台（时空数据治理与共享交换）",
                           "业务应用平台（运行调度、工单、档案）",
                           "运维支撑平台（巡检、备件、寿命与预警）"],
        "terms": [
            ("清淤", "清除渠道/塘堰内淤积泥沙，恢复过流能力；常按立方米计量并单列预算。"),
            ("节制闸", "调节水位与流量的控制建筑物，可启闭挡水、放水。"),
            ("暗渠", "地下输水通道，用于跨越道路或居民区，检修需进入检查井。"),
            ("排涝泵站", "以机泵抽取区内积水外排，核心参数含流量、扬程、装机功率。"),
            ("雨量站", "观测降雨量的站点，常用翻斗式雨量计，单位毫米。"),
            ("过流能力", "渠道/建筑物在无压状态下能安全通过的最大流量。"),
            ("三端四平台", "本工程信息化架构：三端指管理端/移动端/大屏端，四平台指监测感知/数据中枢/业务应用/运维支撑。"),
        ],
        "sections": [
            "一、背景与现状（工程规模、历年问题、信息化基础）",
            "二、总体架构（三端四平台一张图）",
            "三、感知层建设（站点布设、设备选型、通信链路）",
            "四、数据中枢（时空数据治理、共享交换）",
            "五、业务应用（运行调度、工单闭环、档案归集）",
            "六、古建与遗产保护专题",
            "七、运行维护（巡检周期、备件、应急预案）",
            "八、成效与下一步计划",
        ],
        "metrics": {"在线率": "在线测点数/应在线测点数",
                    "告警及时率": "按时推送告警数/总告警数",
                    "工单闭环率": "已闭环工单/下发工单",
                    "数据入库率": "入库记录/采集记录"},
        "sources": ["水利部 water.gov.cn / 省级水利厅公告",
                    "SL 系列水利行业标准", "工程竣工验收与调度运行资料"],
        "import_tpl": {
            "name": "水利工程资料卡",
            "fields": ["工程名称", "所在位置", "类型（渠道/闸/泵站/涵洞）",
                       "规模参数", "建成年代", "现状问题", "管护单位"],
        },
    },
    "heritage": {
        "name": "水利古建遗产",
        "note": "古建本体与水工设施合一的遗产类型，侧重形制、病害、修缮与监测。",
        "tri_ends": ["管理端（遗产档案与修缮审批）",
                     "移动端（现场勘查与病害记录）",
                     "展示端（数字展陈 / 讲解）"],
        "four_platforms": ["监测感知平台（裂缝、位移、湿度、倾斜）",
                           "数据中枢平台（测绘与档案数据治理）",
                           "业务应用平台（修缮工单与工序验收）",
                           "运维支撑平台（环境调控与灾害预警）"],
        "terms": [
            ("木构", "以立柱、梁枋、斗栱构成的承重体系，核心风险为腐朽、虫蛀、变形。"),
            ("斗栱", "柱头之上承托屋檐的层叠构件，兼受力与装饰，层数和出挑是等级标志。"),
            ("彩画", "梁枋、斗栱表面的彩绘保护层，兼防腐与等级标识，修复需按原制配色。"),
            ("砖石砌体", "碑、塔、堤、闸的砌筑部分，核心风险为风化、空鼓、冻融剥落。"),
            ("碑刻", "刻铭石碑，记录工程规制与修缮沿革，是断代与志证的关键实物。"),
            ("空鼓", "砌体与基层脱离形成的空腔，敲击有鼓音，是加固前必须处理的隐患。"),
            ("含水率", "木材含水率升高意味着腐朽风险，现场常用电阻率法粗判。"),
        ],
        "sections": [
            "一、古建概况（历史沿革、形制等级、与水工关系）",
            "二、现状勘察（木构/砖石/彩画/碑刻的分项状况）",
            "三、病害诊断（成因分析：水侵、风化、虫腐、冻融）",
            "四、修缮方案（原制 preservation 原则、材料替换、工序）",
            "五、监测保护（传感器布点、环境调控、日常养护）",
            "六、数字化建档（测绘、三维、影像与档案归集）",
            "七、成效与下一步",
        ],
        "metrics": {"病害处置率": "已处置病害点数/普查病害点数",
                    "修缮验收合格率": "一次验收合格项/验收项",
                    "监测覆盖率": "布设监测点/重点部位总数",
                    "档案归集率": "数字化归档件/应归档件"},
        "sources": ["中国古迹遗址保护协会 ihchina.cn",
                    "古建筑木结构维护与加固技术规范",
                    "文物/世界遗产年度保护报告"],
        "import_tpl": {
            "name": "古建单体资料卡",
            "fields": ["名称", "年代", "结构类型", "规模", "保存状况", "主要病害", "修缮记录"],
        },
    },
    "sensor": {
        "name": "感知应用 · 三端四平台",
        "note": "感知层建设：测什么、在哪测、怎么传、如何告警联动。",
        "tri_ends": ["管理端（设备台账与告警处置）",
                     "移动端（外业校核与现场处置）",
                     "大屏端（一张图值守与可视化）"],
        "four_platforms": ["监测感知平台（采集与边缘计算）",
                           "数据中枢平台（时序库与标准编码）",
                           "业务应用平台（告警工单与联动）",
                           "运维支撑平台（在线率与寿命管理）"],
        "terms": [
            ("雨量计", "翻斗式雨量传感器，输出累计雨量与分钟雨强，需防堵、防倾。"),
            ("水位计", "雷达/浮子/压力式，用于渠道与水库水位，量程与精度按等级选。"),
            ("流量", "常用断面法或堰流公式推算，在线测流多用超声时差法。"),
            ("在线率", "在线设备数 / 应在线设备数，是感知系统最重要的运维指标。"),
            ("边缘计算", "在网关侧完成清洗、阈值判断与压缩上传，降低中心压力与流量费用。"),
            ("告警联动", "阈值越限自动触发工单、推送与视频复核，形成闭环。"),
            ("通信链路", "4G、专网、光纤、北斗短报几种方式，按功耗、带宽与成本选配，关键测点留双链路。"),
        ],
        "sections": [
            "一、感知对象（测什么：雨量、水位、流量、闸泵、视频）",
            "二、站点布设（布点原则、密度、安装形式与防冲刷）",
            "三、设备选型（量程、精度、供电、防护等级、功耗）",
            "四、通信与边缘（链路方案、边缘清洗、断线补传）",
            "五、告警与联动（阈值、分级、复核、工单闭环）",
            "六、运维保障（在线率、标定周期、备件与巡检）",
            "七、成效与下一步",
        ],
        "metrics": {"设备在线率": "在线设备/应在线设备",
                    "采集完整率": "有效数据条数/应采条数",
                    "告警准确率": "复核属实告警/总告警",
                    "平均修复时长": "从告警到恢复的平均小时数"},
        "sources": ["感知厂商技术文档（海康/大华/华为）",
                    "水文/气象观测国家标准 GB/T", "本项目测点台账与运维记录"],
        "import_tpl": {
            "name": "测点资料卡",
            "fields": ["测点编号", "类型", "桩号/位置", "设备型号", "安装日期",
                       "供电方式", "通信方式", "养护记录"],
        },
    },
}


def _default_all():
    return json.loads(json.dumps(DEFAULT_PROFILES))


# ==================================================================
# 读写（每次读盘，界面改完立刻生效）
# ==================================================================
def load_all():
    """读盘；文件不存在或坏了都退回内置默认值。"""
    data = _default_all()
    try:
        if os.path.exists(FILE_WATER):
            disk = json.load(open(FILE_WATER, encoding="utf-8"))
            if isinstance(disk, dict):
                for k, v in disk.items():
                    data[k] = v
    except Exception:
        pass
    return data


def list_profiles():
    all_p = load_all()
    return [{"key": k, "name": v.get("name", k), "note": v.get("note", ""),
             "sections": len(v.get("sections") or []),
             "terms": len(v.get("terms") or []),
             "tri_ends": v.get("tri_ends") or [],
             "four_platforms": v.get("four_platforms") or []}
            for k, v in all_p.items()]


def get(key="water"):
    p = load_all().get(key)
    if not p:
        return {"ok": False, "error": "没有这个领域档：%s" % key}
    return {"ok": True, "key": key, **p}


def update(key, patch):
    """局部更新并落盘。关键字段（tri_ends / four_platforms / terms / sections）按列表整体替换。"""
    data = load_all()
    if key not in data:
        data[key] = json.loads(json.dumps(DEFAULT_PROFILES.get("water", {})))
    cur = data[key]
    for k, v in (patch or {}).items():
        if v is None:
            continue
        if isinstance(v, list) and v and isinstance(v[0], list):
            # 术语表是 [[词, 释义], ...]
            cur[k] = [[str(a), str(b)] for a, b in v]
        elif isinstance(v, list):
            cur[k] = [str(x) for x in v]
        elif isinstance(v, dict):
            cur[k] = v
        else:
            cur[k] = v
    cur.setdefault("name", key)
    data[key] = cur
    os.makedirs(os.path.dirname(FILE_WATER), exist_ok=True)
    with open(FILE_WATER, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "key": key, "name": cur.get("name")}


# ==================================================================
# 派生：给 PPT / 知识库 / 问答用的文本
# ==================================================================
RE_PAREN = re.compile(r"[（(][^）)]*[)）]")


def _title_of(section):
    """「二、总体架构（三端四平台一张图）」→「总体架构」"""
    t = RE_PAREN.sub("", section)
    t = t.split("、")[-1].strip()
    return t


def outline(key="water", topic="", pages=8):
    """按领域骨架 + 主题，产出 PPT 页（不调模型，纯结构化，稳）。
    页数按 pages 收敛：末页固定「总结与下一步」。"""
    p = load_all().get(key) or {}
    secs = list(p.get("sections") or DEFAULT_PROFILES["water"]["sections"])
    secs = [_title_of(s) for s in secs if _title_of(s)]
    if topic:
        # 有主题就改写「背景与现状」这一节，别再造一个重复页
        secs[0] = "%s · 背景与现状" % topic if secs else "%s" % topic
    keep = max(2, int(pages or 8) - 1)
    secs = secs[:keep]

    tri = [x for x in (p.get("tri_ends") or []) if x]
    plats = [x for x in (p.get("four_platforms") or []) if x]
    slides = []
    for t in secs:
        bullets = []
        if "架构" in t or "总体" in t:
            bullets = tri[:3] + plats[:4]
        elif "感知" in t or "监测" in t:
            bullets = tri[:3] or plats[:4]
        elif "数据" in t or "中枢" in t:
            bullets = plats[:4]
        elif "应用" in t or "业务" in t:
            bullets = plats[2:4] or plats[:4]
        elif "运维" in t or "维护" in t or "保障" in t:
            bullets = plats[-1:] or plats[:4]
        slides.append({"title": t, "bullets": bullets,
                       "layout": "bullets" if bullets else "text"})
    slides.append({"title": "总结与下一步", "bullets": ["主要结论", "待办事项", "责任分工与时限"],
                   "layout": "bullets"})
    return slides


def kb_entry(key="water"):
    """领域说明文本，可直接入库或塞进问答提示词，保证口径一致。"""
    p = load_all().get(key) or {}
    if not p:
        return ""
    L = ["# %s\n" % p.get("name", key)]
    if p.get("note"):
        L.append("> %s\n" % p["note"])
    if p.get("tri_ends"):
        L.append("\n## 三端\n")
        L += ["- %s" % x for x in p["tri_ends"]]
    if p.get("four_platforms"):
        L.append("\n## 四平台\n")
        L += ["- %s" % x for x in p["four_platforms"]]
    if p.get("metrics"):
        L.append("\n## 指标口径\n")
        for k, v in p["metrics"].items():
            L.append("- **%s**：%s" % (k, v))
    if p.get("terms"):
        L.append("\n## 术语口径\n")
        for w, d in p["terms"]:
            L.append("- **%s**：%s" % (w, d))
    if p.get("sources"):
        L.append("\n## 权威来源\n")
        L += ["- %s" % x for x in p["sources"]]
    return "\n".join(L)


def graph_terms(key="water"):
    """该领域的术语名词表（给 kb_graph 当图谱种子节点用）。

    谱系里最要紧的那批词（清淤、节制闸、斗栱、在线率…）形态正则抽不全，
    但恰恰是顺着概念链追问时最需要的，所以从领域包显式灌进去。"""
    p = load_all().get(key) or {}
    out = []
    for w, _d in (p.get("terms") or []):
        w = w if isinstance(w, str) else (w[0] if isinstance(w, (list, tuple)) else "")
        if isinstance(w, str) and 2 <= len(w) <= 12:
            out.append(w)
    return out


def metrics_text(key="water"):
    """指标口径（三端四平台常态要拿它写report/PPT）。"""
    p = load_all().get(key) or {}
    m = p.get("metrics") or {}
    return "\n".join("- **%s**：%s" % (k, v) for k, v in m.items())


def qa(key="water", question="", kb="myme_kb", **kw):
    """领域口径 + 本地知识库 + ReAct Agent 的一站式答疑。

    走 kb_agent 而不是 kb_qa，是为了让复杂问题（要跨文档、要联网核最新口径）
    能自己规划步骤；简单问题也照样能答。"""
    import kb_agent
    return kb_agent.run(question, kb=kb, domain=key, **kw)


def qa_hint(key="water"):
    """把领域口径压成一句提示词前缀，接在问答前面。"""
    p = load_all().get(key) or {}
    if not p:
        return ""
    names = [x.split("（")[0].split("(")[0].strip() for x in (p.get("tri_ends") or [])]
    plats = [x.split("（")[0].split("(")[0].strip() for x in (p.get("four_platforms") or [])]
    parts = []
    if names and plats:
        parts.append("按「三端（%s）+ 四平台（%s）」的口径回答" % ("、".join(names), "、".join(plats)))
    if p.get("terms"):
        parts.append("术语按下述口径（释义以知识库原文为准）：" +
                     "、".join(w for w, _d in p["terms"][:12]))
    if p.get("metrics"):
        parts.append("指标口径：" + "、".join(p["metrics"].keys()))
    return "；".join(parts)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="水利古建 / 感知应用 领域包")
    ap.add_argument("act", choices=["list", "show", "outline", "kb", "hint"])
    ap.add_argument("--key", default="water")
    ap.add_argument("--topic", default="")
    ap.add_argument("--pages", type=int, default=8)
    a = ap.parse_args()
    if a.act == "list":
        print(json.dumps(list_profiles(), ensure_ascii=False, indent=2))
    elif a.act == "show":
        print(json.dumps(get(a.key), ensure_ascii=False, indent=2))
    elif a.act == "outline":
        print(json.dumps(outline(a.key, a.topic, a.pages), ensure_ascii=False, indent=2))
    elif a.act == "kb":
        print(kb_entry(a.key))
    else:
        print(qa_hint(a.key))


if __name__ == "__main__":
    main()
