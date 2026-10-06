# -*- coding: utf-8 -*-
"""把 version_info.txt 的版本字段同步成当前 config.VERSION。

为什么存在（历史坑）：
  myme.spec 用 version_info.txt 里的 FileVersion / ProductVersion 生成 exe 的版本资源，
  而这个文件原先是手写的 2.6.4.0。一升级源码到 2.6.5，msi / deb 名字都跟着变了，
  **exe 右键属性却还写着 2.6.4.0** —— 包装里"源码 ≠ 产物"，排查时会怀疑自己打错了包。
  打包链（pack_full.py / pack_portable.py / build_ios_zip.py / spec 之前）先跑本脚本即可。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    import config
    ver = str(getattr(config, "VERSION", "")).strip()
    if not ver:
        raise SystemExit("[sync_version_info] 读不到 config.VERSION")
    v4 = ".".join((ver.split(".") + ["0", "0", "0"])[:4])

    p = os.path.join(HERE, "version_info.txt")
    old = None
    with open(p, "r", encoding="utf-8") as f:
        old = f.read()

    def _rep(m):
        # 只补版本字面量，StringStruct('FileVersion', '...') 的键名与结构不动
        return m.group(1) + v4 + "')"

    new = re.sub(r"(StringStruct\('(?:File|Product)Version', ')[^']+'\)", _rep, old)
    if new != old:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"[sync_version_info] {os.path.basename(p)}: 旧版本资源 -> {v4}")
    print(f"[sync_version_info] 版本资源 {v4}（config.VERSION={ver}）")
    return v4


if __name__ == "__main__":
    main()
