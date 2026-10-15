# -*- coding: utf-8 -*-
"""把交接目录打成总包（handover_<name>.zip）。

为什么单独立这个脚本：交接包每版内容大同小异，却一直是手工勾文件，
漏一项、塞进 dist 就很难被发现；这里把「交接说明 / 产物清单 / 源码 / 关键源码」固定成清单。

用法：
  python pack_handover.py                       # 用 config.VERSION 与当天日期
  python pack_handover.py 2.6.9 2026-10-07
"""
import os
import sys
import zipfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config  # noqa: E402

VER = sys.argv[1] if len(sys.argv) > 1 else config.VERSION
DATE = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%Y-%m-%d")
NAME = "handover_myne_%s" % DATE
# 第 3 个参数可指定交接目录名（沿用上一版目录名时用它，免得交接包spath 又多一层）
OUT_DIR_NAME = sys.argv[3] if len(sys.argv) > 3 else NAME
OUT_DIR = os.path.join(HERE, OUT_DIR_NAME)
OUT = os.path.join(OUT_DIR, "%s.zip" % NAME)

# 关键源码：交接包要能让人照着把问题复现一遍
KEY_SRC = [
    "app.py", "avatar.py", "config.py", "persons.py", "knowledge_base.py",
    "sync_version_info.py", "version_info.txt", "pack_full.py", "pack_portable.py",
    "build_ios_zip.py", "gen_en_narration.py", "publish_release.py",
    "tts_worker.py", "tts_service.py", "pack_source.py", "narrate.py", "qa_brain.py",
    "runtime_check.py", "_chk_seg.py",   # runtime_check：换机一键体检；_chk_seg：四段复验
    # v2.9 深度加工四件套 + 知识库增强
    "kb_graph.py", "kb_agent.py", "kb_editor.py", "kb_trace.py", "kb_qa.py",
    "kb_wiki.py", "kb_chunking.py",
    # v2.8 多源搬运 + 领域包
    "qiaomu_local.py", "domain_water.py",
    # v2.6 PPT 制作链
    "make_ppt.py", "pptx_write.py", "ppt_templates.py", "pptx_parse.py",
    "deck_narration.py", "doc_ingest.py", "doc_write.py",
    # 分身语音/视频
    "avatar_service.py", "avatar_sonic.py", "asr_listen.py",
]

OTHER = ["README.md", "ui/index.html", "ui/player.html", "tts_service.py"]


def main():
    if not os.path.isdir(OUT_DIR):
        print("[pack_handover] 交接目录不存在：", OUT_DIR)
        return 1
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=4) as z:
        for fn in ("交接说明.md", "产物清单.txt"):
            p = os.path.join(OUT_DIR, fn)
            if os.path.isfile(p):
                z.write(p, fn); n += 1
        src = os.path.join(HERE, "dist", "myme_source_%s.zip" % VER)
        if os.path.isfile(src):
            z.write(src, "myme_source_%s.zip" % VER); n += 1
        for k in KEY_SRC:
            p = os.path.join(HERE, k)
            if os.path.isfile(p):
                z.write(p, k); n += 1
            else:
                print("  ⚠ 交接包缺关键源码：", k)
        for k in OTHER:
            p = os.path.join(OUT_DIR, k)
            if os.path.isfile(p):
                z.write(p, k); n += 1
        # 两个可执行文件都要：完整版（安装即用）+ 便携播放版（播放端复现）
        for rel, arc in (("dist/myme.exe", "myme.exe"),
                         ("dist/myme-play.exe", "myme-play.exe")):
            p = os.path.join(HERE, rel)
            if os.path.isfile(p):
                z.write(p, arc); n += 1
            else:
                print("  ⚠ 交接包缺产物：", rel)
    print("[pack_handover] %s（%d 项，%.1f MB）" % (OUT, n, os.path.getsize(OUT) / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
