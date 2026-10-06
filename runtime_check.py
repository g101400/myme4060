#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""换机 / 新环境一键体检（runtime_check.py）

用途：把「这台机器能跑全链路吗」压缩成一条命令的回答。
换机、换盘、重装系统、克隆完源码之后，先跑它，再决定要不要补件、补哪一件。

    python runtime_check.py                 # 人类可读报告
    python runtime_check.py --json          # 机器可读（贴给运维/贴 issue）
    python runtime_check.py --strict        # 只报「必须项」，缺一件就退出码 1
    echo $?                                 # 1 = 有必须项缺失；0 = 全齐

体检分五组：
  ① 运行时依赖（ComfyUI / 内置 Python / LivePortrait / ts_deps / ffmpeg）
  ② 外部服务（Ollama 11434、TTS 8777、数字人 8778、主程序 7860/7861）
  ③ 数据根（DATA_DIR 指向哪儿、当前版本几页、四段 ready 不 ready）
  ④ 导出的四段（分身 × 语言：炎冰中 / 炎冰英 / 金子中 / 金子英）
  ⑤ 本脚本自己能不能跑（python 版本、能不能 import app）

设计原则（踩过的坑）：
  · 「查不到」不等于「没生成」。③ 组会把数据根打出来，避免对着仓库目录查一通误判失败；
    真正的数据根是 MYME_DATA，没设时 frozen 版走 %LOCALAPPDATA%\\myme。
  · 必须项缺失 ≠ 全链路废掉：LivePortrait/ts_deps 缺只是数字人退化成「静态肖像 + 配音」，
    所以分开标 [必须]/[可选]，别让用户一看红就以为整套报废。
"""

import json
import os
import socket
import sys

OK = "✓"
BAD = "✗"


def _hz(s):
    """终端不乱码地打勾/叉。"""
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        pass


def _probe_http(url, timeout=3):
    """探活：只打 /ready 这类真端点，别打根路径（打根路径会吃 404 被当成服务不在）。"""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "replace")[:160]
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, type(e).__name__


def _port_open(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def main():
    strict_only = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    lines = []
    result = {"runtime": {}, "services": {}, "data": {}, "segments": None, "ok": True}

    # ---------- ① 运行时依赖 ----------
    items = []
    try:
        import app_settings
        items = app_settings.runtime_status().get("items", [])
    except Exception as e:
        items = []
        lines.append("  运行时自检不可用：%s" % e)
    for it in items:
        result["runtime"][it["key"]] = {"ok": it["ok"], "path": it["path"], "name": it["name"]}
        if not it["ok"]:
            result["ok"] = False
    if not strict_only:
        lines.append("【① 运行时依赖】")
        for it in items:
            tag = "必须" if it.get("must") else "可选"
            line = "  %s %-28s [%s] %s" % (OK if it["ok"] else BAD, it["name"], tag, it["path"] or "（未登记）")
            lines.append(line)
            if not it["ok"]:
                lines.append("        补法：%s" % it.get("how", ""))
                lines.append("        影响：%s" % it.get("why", ""))
        lines.append("")

    # ---------- ② 外部服务 ----------
    services = [
        ("TTS 语音合成", "http://127.0.0.1:8777/ready"),
        ("数字人口型", "http://127.0.0.1:8778/ready"),
        ("主程序 7860", "http://127.0.0.1:7860/api/meta"),
        ("便携播放 7861", "http://127.0.0.1:7861/api/meta"),
        ("Ollama 1134", "http://127.0.0.1:11434/api/tags"),
    ]
    lines.append("【② 外部服务】")
    lp_missing = any((not it["ok"]) for it in items if it["key"] == "liveportrait")
    for name, url in services:
        ok, info = _probe_http(url)
        result["services"][name] = {"ok": ok, "url": url}
        note = ""
        if name == "数字人口型" and not ok and lp_missing:
            note = "  ← 已登记 LivePortrait 才会起；现在缺件，属预期（演示退化成静态肖像+配音）"
        lines.append("  %s %-16s %s %s%s" % (OK if ok else BAD, name, url, info if ok else info, note))
    lines.append("")

    # ---------- ③ 数据根 ----------
    try:
        import config
        dd = getattr(config, "DATA_DIR", "")
    except Exception as e:
        dd, _ = "", str(e)
    pages, segs, cur_ver, other_vers = 0, None, "", 0
    try:
        import app
        d = json.load(open(config.SLIDES_FILE, encoding="utf-8")) or {}
        vers_map = d.get("versions") or {}
        meta = d.get("meta") or {}
        order = [v.get("id") for v in (meta.get("versions") or [])] or list(vers_map.keys())
        cur_ver = meta.get("currentVersion") or (order[0] if order else "")
        vers = [vers_map[k] for k in order if k in vers_map]
        persons = list((app.persons.list_persons() or {}).get("persons") or [])
        # 只把【当前版本】这一份喂进去，_build_segments 才会刚好产出 4 段；
        # 传全部版本会连历史版本一起列出，历史版本英文没补是常态，容易误判成缺陷。
        segs = app._build_segments([vers_map[cur_ver]] if cur_ver in vers_map else vers[-1:], persons) if (
            cur_ver in vers_map or vers) else []
        pages = sum(len((v or {}).get("slides") or []) for v in vers) or 0
        other_vers = max(len(vers_map) - 1, 0)
    except Exception as e:
        lines.append("  %s 四段统计失败：%s" % (BAD, e))
    result["data"] = {"dir": dd, "exists": bool(dd and os.path.isdir(dd)),
                      "current_version": cur_ver, "versions": other_vers + 1}
    lines.append("【③ 数据根】")
    exists = bool(dd and os.path.isdir(dd))
    lines.append("  %s DATA_DIR = %s" % (OK if exists else BAD, dd or "（未解析出来）"))
    lines.append("        这是真数据所在，别拿源码目录当数据根查；复验请用：MYME_DATA=\"%s\" python _chk_seg.py" % dd)
    lines.append("        当前版本 = %s；历史版本 %s 个（历史版本的英文配音本来就没补，不算缺陷）"
                 % (cur_ver or "（未定）", other_vers))
    lines.append("        全部版本页数合计 = %s" % pages)
    lines.append("")

    # ---------- ④ 四段（只看当前版本）----------
    if segs and cur_ver:
        lines.append("【④ 导出四段（分身 × 语言）· 只验当前版本 %s】" % cur_ver)
        grp = segs  # 已经只喂了当前版本，这 4 段就是验收对象
        good_segs = [s for s in grp if s["ready"] and s.get("pages_ready") == s.get("pages")]
        for s in grp:
            lines.append("  %s %-12s ready=%-5s %s/%s 页 %s" % (
                OK if s["ready"] else BAD, s.get("key") or s.get("id"), s["ready"],
                s.get("pages_ready"), s.get("pages"), s.get("missing") or ""))
        good = len(good_segs) == 4
        lines.append("  → 四段全齐（换脸必须换声、切英文必须换配音，靠这四段验收）：%s" % ("是，可以导" if good else "否"))
        if not good:
            result["ok"] = False
        lines.append("")

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n".join(l for l in lines if l is not None))
        print("=" * 60)
        print("结论：%s" % ("全齐，可以跑全链路" if result["ok"] else "有缺失，按上面 [必须] 项补件后再跑"))

    if strict_only:
        # 只因「必须项」缺失退出 1（可选项缺不挡路）
        need_fail = any((not v["ok"]) for k, v in result["runtime"].items()
                        if k in ("comfyui_root", "embedded_py", "ffmpeg"))
        need_fail = need_fail or not result["data"]["exists"]
        return 1 if need_fail else 0
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
