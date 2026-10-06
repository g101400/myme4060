# -*- coding: utf-8 -*-
"""
myme.pack_runtime — 打「随行运行包」runtime.7z（LivePortrait + ts_deps）

为什么需要它
------------
LivePortrait 约 647MB（含 608MB 权重）+ ts_deps 约 129MB，都是**重依赖**：
  - 不塞进 282MB 的 myme.exe（塞进去 exe 要涨到 1GB+ 且首启解压极慢）；
  - 也不该进源码包 myme_source_*.zip（clone 给别人还得再下 700MB）；
  - 但要随交代对象走，换机解压到同一层就能直接跑数字人口型。

约定：**runtime 与 myme 源码/其他包同层**。解压后是

    LivePortrait/            （含 pretrained_weights/608MB 权重）
    ts_deps/                 （transformers 4.57 隔离包）
    myme/                    （源码或 exe）
    dist/myme_runtime_<ver>.7z   ← 本脚本产物

用法：
    python pack_runtime.py              # 2.6.9 → dist/myme_runtime_2.6.9.7z
    python pack_runtime.py 2.6.9        # 显式指定版本
    python pack_runtime.py --dry        # 只报文件与预估大小，不真打
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))  # myme 源码根（= config.WORKSPACE）
# ⚠ LivePortrait / ts_deps 必须落在 myme 目录**内部**，原因：config._resolve_lp_repo()
#   在源码态（非冻结）只探 WORKSPACE/LivePortrait 与「脚本同级目录/LivePortrait」，
#   放在 myme 的上一级（…/Claw/LivePortrait）反而一个都命中不了 → 换机体检会判缺依赖。
#   早期版本按「与 myme 同层」打包，结果换机解压后 runtime_check 仍报 LivePortrait 缺失。
ROOT = HERE

# 7z 常见位置
SZ_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "7-Zip", "7z.exe"),
    "7z", "7za",
]

# 必须同时存在的判据文件 —— 只有目录在、判据文件不在，说明是半截目录，宁可报错
MUST_HAVE = {
    "LivePortrait": (os.path.join("src", "config", "inference_config.py"),),
    "ts_deps": (os.path.join("transformers", "__init__.py"),),
}


def find_7z():
    for c in SZ_CANDIDATES:
        if c and os.path.isfile(c):
            return c
        if c and os.path.isabs(c):
            continue
    for c in ("7z", "7za"):                        # 已在 PATH
        from shutil import which
        w = which(c)
        if w:
            return w
    return ""


def dir_mb(path):
    total = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total / 1048576.0


def main():
    ver = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "2.6.9"
    dry = "--dry" in sys.argv

    root = ROOT
    if not os.path.isdir(root):
        root = HERE                                # 兜底：父目录不存在就就地打
        print("[warn] 未找到 Claw 父目录，改用 %s" % root)

    targets = []
    for name in MUST_HAVE:
        p = os.path.join(root, name)
        if not os.path.isdir(p):
            print("[✗] 缺少 %s（期望 %s）" % (name, p))
            return 2
        for rel in MUST_HAVE[name]:
            if not os.path.isfile(os.path.join(p, rel)):
                print("[✗] %s 不完整：缺 %s" % (name, rel))
                return 2
        targets.append(p)

    total = sum(dir_mb(p) for p in targets)
    print("待打随行包：")
    for p in targets:
        print("  %-60s %8.1f MB" % (p, dir_mb(p)))
    print("  合计 %8.1f MB → 压后预计 %.0f%%" % (total, total * 0.55))

    out_dir = os.path.join(root, "dist")
    if not dry:
        os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "myme_runtime_%s.7z" % ver)
    print("输出：%s" % out)

    if dry:
        print("[dry] 跳过实际压缩")
        return 0

    sz = find_7z()
    if not sz:
        print("[✗] 没找到 7z（装 7-Zip 或把 7z.exe 放进 PATH）")
        return 3

    # ⚠ 必须用「相对路径 + 在 myme 目录下执行」：7z 会把给进去的路径原样存进包里。
    #   给绝对路径会存成 myme/LivePortrait，换机解压出来多套一层 myme/，
    #   而 runtime_check.py 找的是同层的 LivePortrait/，直接判失败——踩过一次。
    names = [os.path.basename(p.rstrip(os.sep)) for p in targets]
    cmd = [sz, "a", "-t7z", out, "-mx=5", "-ms=on", "-bd", "-y"] + names
    print("执行（cwd=%s）：%s" % (HERE, " ".join('"%s"' % c for c in cmd)))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    dt = time.time() - t0
    tail = "\n".join((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:]
    print("退出码 %d，用时 %.1fs" % (r.returncode, dt))
    for line in tail:
        print("  " + line)
    if r.returncode != 0:
        print("[✗] 压缩失败")
        return r.returncode

    size = os.path.getsize(out) / 1048576.0
    print("[✓] 随行包 ready：%.1f MB（原始 %.1f MB，压缩到 %.0f%%），用时 %.1fs"
          % (size, total, size / total * 100, dt))
    print("换机用法：解压出 LivePortrait/ 和 ts_deps/，放进 **myme 源码/exe 所在目录内**（与 myme.exe 同级），")
    print("再跑 `python runtime_check.py` 看依赖是否全绿 —— 别解压到外层目录，")
    print("config._resolve_lp_repo() 只认 myme 目录内部的那一份，放外面会一直报缺依赖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
