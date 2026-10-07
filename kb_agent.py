# -*- coding: utf-8 -*-
"""
kb_agent.py — ReAct Agent 层（吸收 WeKnora v0.8.2 第二层：ReAct Agent）

WeKnora 的第二层不是「再检索一次」，而是让 Agent 自己规划：
    先搜知识库 → 再联网查最新数据 → 再调工具读数据看板 → 最后整合成报告，
全程能看到每一步在想什么、调了什么工具，并且支持**人工审批节点**。

myme 这里按本机条件落地成一套「本地知识库 + 国内可达联网 + 领域口径」的三工具循环：

    Thought → Action → Observation → … → Final

  可用动作（工具）：
    kb_search(q)      本机知识库混合检索（向量+BM25+图谱邻居扩展）
    web_search(q)     国内搜索引擎兜底（必应 → 百度，取代原项目的 Google / r.jina.ai）
    fetch(url)        直连抓网页正文（国内站点）
    domain(k)         灌领域口径（water 水利 / heritage 古建 / sensor 感知，含三端四平台）
    finish(answer)    收尾

  ★ 人工审批：approve=True 时，Agent 在「联网/抓网页」这类有外部副作用或不确定性的动作前
    会挂起（返回 pending），等外部确认（confirm）再继续执行 —— 不是放飞自我的 autonomy。

状态落到 KP_ROOT/agent/<id>.json，UI 可轮询进度、可单独确认某一步。

用法：
    import kb_agent
    r = kb_agent.run("清淤工期多少钱？")          # 全自动跑完
    r = kb_agent.run("最新政策是什么？", approve=True)  # 联网前会停下来等确认
    kb_agent.confirm(agent_id)                    # 外部确认后继续跑完
CLI:
    python kb_agent.py "XX 的验收标准是什么？"
"""

import os
import re
import json
import time
import uuid

import config

TOOLS = {
    "kb_search":   "在本机知识库里检索：参数是一个检索式（中文原话即可），返回带出处的片段",
    "web_search":  "国内搜索兜底（必应/百度）：参数是一个检索式，返回标题+链接线索",
    "fetch":       "直连抓一个网页正文：参数是 http(s) 链接",
    "domain":      "灌领域口径（不消耗、只作为常识注入）：参数是 water 或 heritage 或 sensor",
    "finish":      "收尾输出最终答案：参数是一个带出处的完整回答",
}

MAX_STEPS = 6
MAX_ACTION_LEN = 600
OBSERVATION_LIMIT = 2200          # 单步观察值塞回上下文的字数上限

PLAN_PROMPT = """你是本机知识库助理，需要按「思考→行动→观察」的多步方式回答问题。
只能使用下面这些工具，每次只输出一个动作的 JSON，不要解释、不要 ``` 包裹：

{tools}

规则：
- 第一步先用 kb_search 查本机知识库（里面通常已有答案，先别上网）；
- 只有知识库查不到、或问题明确要「最新」时，才用 web_search / fetch；
- 查到的每条事实都要保留出处（片段标题/链接）；
- 最多 {max_steps} 步，到点必须调用 finish。

输出格式（严格 JSON，不要多余文字）：
{{"thought":"我在想什么，准备怎么答","action":"工具名","action_input":"工具参数"}}

问题：{q}

JSON："""


def agent_dir():
    d = os.path.join(getattr(config, "KP_ROOT", None) or config.DIR_DATA, "agent")
    os.makedirs(d, exist_ok=True)
    return d


def _state_path(aid):
    return os.path.join(agent_dir(), "%s.json" % aid)


# ============================================================
# 工具箱（每个都自己吞异常，绝不让 Agent 因一个工具挂掉而整体崩）
# ============================================================
def _t_kb_search(q, names=None):
    import kb_qa
    cands = kb_qa.retrieve_multi(q, names=(names or ["myme_kb"]), top_k=5, expand=True)
    if not cands:
        return "（没查到）"
    parts = []
    for i, c in enumerate(cands[:6], 1):
        loc = c.get("label") or c.get("source") or "知识库"
        bc = c.get("breadcrumb")
        parts.append("【%d】%s%s\n%s" % (i, (bc + " · " if bc else "") + loc,
                                        "（图谱扩展）" if c.get("graph") else "",
                                        (c.get("text") or "")[:700]))
    return "\n\n".join(parts)[:OBSERVATION_LIMIT]


def _t_web_search(q):
    try:
        import qiaomu_local
        hits = qiaomu_local._web_search(q, limit=5)
    except Exception as e:
        return "（联网失败：%s）" % e
    if not hits:
        return "（国内搜索没返回可用结果）"
    return "\n".join("%s\n%s" % (h["title"], h["url"] or "（无直达链接）") for h in hits)


def _t_fetch(url):
    try:
        import qiaomu_local
        text, meta = qiaomu_local.fetch_url(url, paywall=True)
    except Exception as e:
        return "（抓取失败：%s）" % e
    title = (meta or {}).get("title") or ""
    return ("【标题：%s】\n%s" % (title, text))[:OBSERVATION_LIMIT]


def _t_domain(key):
    try:
        import domain_water
        hint = domain_water.qa_hint(key)
        outline = domain_water.outline(key, "该领域要点", pages=3)
    except Exception as e:
        return "（领域口径注入失败：%s）" % e
    return (hint or "") + "\n" + str(outline)[:OBSERVATION_LIMIT]


CALL = {"kb_search": _t_kb_search, "web_search": _t_web_search,
        "fetch": _t_fetch, "domain": _t_domain}


# ============================================================
# 主循环
# ============================================================
def run(question, names=None, kb="myme_kb", domain=None, max_steps=MAX_STEPS,
        approve=False, budget_chars=6000):
    """跑一轮 ReAct。返回 {agent_id, answer, steps, tool_calls, secs, pending, status}。

    approve=True 时遇到联网类动作会停下来等 confirm()，返回里 pending 指向那一步；
    确认（或拒绝）之后由 confirm() 接着跑，所以这里的状态必须落盘。"""
    import llm_client
    names = names or [kb]
    aid = time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    state = {"id": aid, "question": question, "steps": [], "status": "running",
             "answer": "", "max_steps": max_steps, "approve": bool(approve),
             "names": list(names), "domain": domain or "", "pending": None,
             "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(aid, state)

    messages = [{"role": "user", "content": PLAN_PROMPT.format(
        tools="\n".join("- %s：%s" % (k, v) for k, v in TOOLS.items()),
        max_steps=max_steps, q=question)}]
    if domain:
        try:
            import domain_water
            hint = domain_water.qa_hint(domain)
            if hint:
                messages.insert(1, {"role": "system", "content":
                                    "【领域口径，回答时优先沿用这些术语与定义】\n" + hint})
        except Exception:
            pass

    t0 = time.time()
    for i in range(max_steps):
        try:
            raw = llm_client.chat(messages, max_tokens=700, temperature=0.2)
        except Exception as e:
            state["status"] = "error"
            state["answer"] = "模型调用失败：%s" % e
            state["steps"].append({"i": i + 1, "type": "llm", "thought": "",
                                   "action": "-", "output": str(e), "secs": 0.0})
            break
        msg, act = _parse(raw)
        messages.append({"role": "assistant", "content": msg})

        action = (act.get("action") or "").strip()
        action_input = str(act.get("action_input") or "").strip()

        if action == "finish":
            state["answer"] = action_input
            state["status"] = "done"
            state["steps"].append({"i": i + 1, "type": "final", "thought": act.get("thought", ""),
                                   "action": "finish", "input": "", "output": action_input,
                                   "secs": round(time.time() - t0, 1)})
            break

        ts = time.time()
        # 联网类动作：审批节点（不是所有环境都需要，UI 里默认开）
        if approve and action in ("web_search", "fetch"):
            state["status"] = "waiting_human"
            state["pending"] = {"i": i + 1, "action": action, "input": action_input,
                                "thought": act.get("thought", "")}
            state["steps"].append({"i": i + 1, "type": "tool", "thought": act.get("thought", ""),
                                   "action": action, "input": action_input,
                                   "output": "（待人工确认）", "pending": True,
                                   "secs": round(time.time() - ts, 1)})
            _save(aid, state)
            return _finish(state, t0)

        if action not in CALL:
            obs = "（不认识的工具 %s，只能用：%s）" % (action, "、".join(CALL))
            messages.append({"role": "user", "content": "观察：%s" % obs})
            state["steps"].append({"i": i + 1, "type": "tool", "thought": act.get("thought", ""),
                                   "action": action or "-", "input": action_input,
                                   "output": obs[:400], "secs": round(time.time() - ts, 1)})
            _save(aid, state)
            continue

        try:
            obs = CALL[action](action_input, names=names) if action == "kb_search" \
                else CALL[action](action_input)
        except Exception as e:
            obs = "（工具 %s 异常：%s）" % (action, e)
        obs = str(obs or "")[:OBSERVATION_LIMIT]
        messages.append({"role": "user", "content": "观察（第%d步）：\n%s" % (i + 1, obs)})
        state["steps"].append({"i": i + 1, "type": "tool", "thought": act.get("thought", ""),
                               "action": action, "input": action_input, "output": obs,
                               "secs": round(time.time() - ts, 1)})
        _save(aid, state)
    else:
        # 步数用尽：手上有什么就答什么，不空手而归
        state["status"] = "done"
        state["answer"] = _fallback_answer(messages, question, budget_chars)

    return _finish(state, t0)


def _finish(state, t0):
    state["secs"] = round(time.time() - t0, 1)
    _save(state["id"], state)
    try:
        import kb_trace
        kb_trace.log({"kind": "agent", "id": state["id"], "question": state["question"],
                      "steps": len(state["steps"]), "status": state["status"],
                      "secs": state["secs"], "domain": state.get("domain", "")})
    except Exception:
        pass
    return _public(state)


def _parse(raw):
    """LLM 返回值 → (assistant 消息文本, 解析出的 dict)。解析不出就当 finish 原文。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    obj = None
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{[^{}]*\}", text, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
            except Exception:
                obj = None
    if isinstance(obj, dict):
        return text, obj
    return text, {"action": "finish", "action_input": text[:MAX_ACTION_LEN],
                  "thought": ""}


def _fallback_answer(messages, question, budget_chars):
    """步数用尽时的兜底：直接用上下文里已有的观察值让模型总结，不另开一轮检索。"""
    import llm_client
    try:
        raw = llm_client.chat(
            [{"role": "user", "content": "下面是多轮检索已经拿到的资料，请直接据此回答，"
                                         "并给每条结论标注出处。步数用尽，不要再调工具。\n\n"
                                         + "\n".join(m.get("content", "") for m in messages)
                                         + "\n\n问题：%s" % question}],
            max_tokens=900, temperature=0.2)
        return (raw or "").strip()
    except Exception as e:
        return "（步数用尽且兜底总结失败：%s）" % e


def _save(aid, state):
    state["tools"] = sorted({s.get("action") for s in state["steps"] if s.get("action")} - {""})
    state["tool_calls"] = len([s for s in state["steps"] if s.get("type") == "tool"])
    try:
        with open(_state_path(aid), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def _public(state):
    return {"agent_id": state["id"], "question": state["question"],
            "status": state["status"], "answer": state["answer"],
            "steps": state["steps"], "tool_calls": state.get("tool_calls", 0),
            "tools": state.get("tools", []), "pending": state.get("pending"),
            "secs": state.get("secs", 0), "created": state.get("created", ""),
            "domain": state.get("domain", "")}


def get(aid):
    p = _state_path(aid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return _public(json.load(f))
    except Exception:
        return None


def confirm(aid, allowed=True, edited_input=None):
    """人工审批确认 / 拒绝：放行后 Agent 从挂起那一步继续跑。

    allowed=False 等于拒绝对外联网，Agent 会换回本地知识库再答。"""
    p = _state_path(aid)
    if not os.path.exists(p):
        return {"error": "找不到这个任务（可能已过期）"}
    try:
        with open(p, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return {"error": "读取任务失败：%s" % e}
    if state.get("status") != "waiting_human":
        return {"error": "这一步已经不需要确认了"}
    pend = state.get("pending") or {}
    action = pend.get("action")
    inp = edited_input if edited_input is not None else pend.get("input")
    for s in state["steps"]:
        if s.get("i") == pend.get("i"):
            s["output"] = "（人工%s）" % ("批准" if allowed else "拒绝")
            s.pop("pending", None)
    state["status"] = "running"
    state["pending"] = None
    _save(aid, state)

    # 接着跑：先把这一步的「新决定」塞回消息流，让模型看到人工的处置
    note = "【人工%s执行 %s：%s】%s" % ("批准" if allowed else "拒绝", action, inp,
                                      "继续按原计划执行" if allowed else
                                      "放弃这一步，改用 kb_search 查本机知识库再答")
    if not allowed:
        state["steps"].append({"i": pend.get("i", 0), "type": "human", "thought": "",
                               "action": action, "input": inp, "output": note, "secs": 0.0})
        _save(aid, state)
        try:
            import llm_client
            raw = llm_client.chat(
                [{"role": "user", "content": "请仅依据本机知识库回答，不要联网。\n\n" + note},
                 {"role": "user", "content": "问题：" + state["question"]}],
                max_tokens=900, temperature=0.2)
            state["answer"] = (raw or "").strip()
            state["status"] = "done"
        except Exception as e:
            state["answer"] = "（人工拒绝后重新作答失败：%s）" % e
            state["status"] = "error"
        return _finish(state, time.time())
    return run_continue(aid, note)


def run_continue(aid, note):
    """放行后从该步继续（复用 run 的循环，但跳过重新规划提示词的重发）。"""
    state = get(aid)
    if not state:
        return {"error": "任务不存在"}
    import llm_client
    import kb_qa
    messages = [{"role": "user", "content": note}]
    t0 = time.time()
    nxt = (max(s.get("i", 0) for s in state["steps"]) + 1) if state["steps"] else 1
    names = state.get("names") or ["myme_kb"]
    for i in range(state.get("max_steps", MAX_STEPS)):
        step_no = nxt + i
        try:
            raw = llm_client.chat(messages, max_tokens=700, temperature=0.2)
        except Exception as e:
            state["answer"] = "模型调用失败：%s" % e
            state["status"] = "error"
            break
        msg, act = _parse(raw)
        messages.append({"role": "assistant", "content": msg})
        action = (act.get("action") or "").strip()
        action_input = str(act.get("action_input") or "").strip()
        if action == "finish":
            state["answer"] = action_input
            state["status"] = "done"
            state["steps"].append({"i": step_no, "type": "final", "thought": act.get("thought", ""),
                                   "action": "finish", "input": "", "output": action_input,
                                   "secs": round(time.time() - t0, 1)})
            break
        ts = time.time()
        if action not in CALL:
            obs = "（不认识的工具 %s）" % action
        else:
            try:
                obs = CALL[action](action_input, names=names) if action == "kb_search" \
                    else CALL[action](action_input)
            except Exception as e:
                obs = "（工具异常：%s）" % e
        obs = str(obs or "")[:OBSERVATION_LIMIT]
        messages.append({"role": "user", "content": "观察（第%d步）：\n%s" % (step_no, obs)})
        state["steps"].append({"i": step_no, "type": "tool", "thought": act.get("thought", ""),
                               "action": action, "input": action_input, "output": obs,
                               "secs": round(time.time() - ts, 1)})
    else:
        state["status"] = "done"
        state["answer"] = _fallback_answer(messages, state["question"], 6000)
    return _finish(state, t0)


def list_recent(n=10):
    d = agent_dir()
    files = sorted([f for f in os.listdir(d) if f.endswith(".json")],
                   reverse=True)[:n]
    out = []
    for f in files:
        try:
            with open(os.path.join(d, f), "r", encoding="utf-8") as fh:
                s = json.load(fh)
            out.append({"agent_id": s.get("id"), "question": s.get("question"),
                        "status": s.get("status"), "secs": s.get("secs"),
                        "created": s.get("created")})
        except Exception:
            continue
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ReAct Agent（本地库 + 国内联网 + 领域口径）")
    ap.add_argument("question")
    ap.add_argument("--kb", default="myme_kb")
    ap.add_argument("--steps", type=int, default=MAX_STEPS)
    ap.add_argument("--domain", default="", help="water / heritage / sensor")
    ap.add_argument("--approve", action="store_true", help="联网前停下来等人工确认")
    a = ap.parse_args()
    r = run(a.question, names=[a.kb], max_steps=a.steps, domain=a.domain or None,
            approve=a.approve)
    print("== %s / %d 步 / %.1fs ==" % (r["status"], len(r["steps"]), r["secs"]))
    for s in r["steps"]:
        print("[%d] %s%s  %s" % (s["i"], s.get("thought") or "",
                                  ("→ " + s["action"] + "(" + (s.get("input") or "")[:40] + ")")
                                  if s.get("action") else "", s.get("output", "")[:160].replace("\n", " ")))
    print("\n---- 答案 ----\n%s" % (r["answer"] or "（无）"))
