# -*- coding: utf-8 -*-
"""临时：打印当前版本导出清单的四片段状态（ready / 缺页 / 缺什么）。"""
import json
import os
import sys

sys.path.insert(0, r"D:\Users\WorkBuddy\Claw\myme")
import config  # noqa: E402
config.init_projects()
import app  # noqa: E402

app._load_slides()
doc = json.load(open(config.SLIDES_FILE, encoding="utf-8")) or {}
m = app._build_manifest(doc, [], 0.0, {})
print("currentVersion:", m.get("currentVersion"))
for s in m.get("segments", []):
    print("  %-11s ready=%-5s %s/%s 页  %s"
          % (s.get("id"), s.get("ready"), s.get("pages_ready"), s.get("pages"),
             s.get("missing") or ""))
