# -*- coding: utf-8 -*-
"""myme v2.9.3 抽包门禁：解包断言"本版标记与关键实现真的在包里"。

为什么必须做（v2.6.5 血的教训）：源码改好 ≠ 包里是新的。打包脚本可能
拿 dist 里的旧 exe 继续打 MSI/SFX，或者 build_apk.sh 中途失败只 `return ""`，
结果**版本号对、内容还是上一版**。所以每一层都从产物里把文件抽出来核。

三层：
  Layer 1  源码层：八处版本号一致
  Layer 2  EXE 层：从 CArchive 抽 ui/index.html / config.py / ui/player.html
  Layer 3  打包产物层：
    · APK   —— assets/player.html 含 v2.9.3/MymeNative/applyParams/pickInput、
               assets/manifest.json 含 params、classes.dex 含 onShowFileChooser/MymeNative、
               aapt2 badging（包名/版本/启动图标）+ apksigner verify
    · win zip —— playback/player.html + manifest.json(params) + README.txt(本播放包标注)
    · deb   —— 逐架构 data.tar.xz 内 opt/myme-player/player.html 含 v2.9.3 + manifest.json(params)
用法： 用装了 PyInstaller 的解释器跑：
  用装了 PyInstaller 的解释器跑 _gate_292.py（版本随 2.9.x 手动改 EXPECT_VCODE）
"""
import io
import json
import lzma
import os
import re
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
sys.path.insert(0, HERE)
import pack_portable as _PP          # 单一真相源：版本号只从打包脚本读
VER = _PP.VER
EXPECT_VCODE = 10                     # 随版本 bump 手动 +1（与 AndroidManifest 对齐）
PKG = "myme-player"
# SDK 位置：环境变量优先，其次自动探测（本机 SDK 曾在 D:/Android，2026-10-15 起装在用户目录）
def _find_build_tools():
    cands = []
    env = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if env:
        cands.append(env)
    home = os.path.expanduser("~")
    cands += [
        os.path.join(home, ".workbuddy", "android-sdk"),
        os.path.join(home, "AppData", "Local", "Android", "Sdk"),
        "D:/Android",
    ]
    for c in cands:
        if not c:
            continue
        if os.path.isdir(c):
            bt_root = os.path.join(c, "build-tools")
            if os.path.isdir(bt_root):
                vers = sorted(os.listdir(bt_root), reverse=True)
                if vers:
                    return c, os.path.join(bt_root, vers[0])
    return (cands[0] if cands else "D:/Android"), None


SDK, BT = _find_build_tools()

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s %s%s" % ("✅" if ok else "❌", name, ("　→ %s" % detail) if detail else ""))


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


# ==================== Layer 1：源码八处 ====================
def l1():
    print("\n=== Layer 1：源码版本号（八处）===")
    check("config.py VERSION==%s" % VER,
          re.search(r'VERSION\s*=\s*"%s"' % re.escape(VER), read(os.path.join(HERE, "config.py"))) is not None)
    vi = read(os.path.join(HERE, "version_info.txt"))
    for needle in ("(%s)" % ", ".join(VER.split(".") + ["0"]),
                   "'%s.0'" % VER):
        check("version_info.txt 含 %s" % needle, needle in vi)
    # 版本号单一真相源：两个打包脚本都从 config.VERSION 动态取（_pkg_ver），
    # 所以不断言字面量，而断言"没有写死的旧版本"且"确实接了 config"。
    pf = read(os.path.join(HERE, "pack_full.py"))
    check("pack_full.py 版本接 config（_pkg_ver）", "_pkg_ver" in pf and 'VER = "2.' not in pf)
    pp = read(os.path.join(HERE, "pack_portable.py"))
    check("pack_portable.py 版本接 config（_pkg_ver）", "_pkg_ver" in pp and 'VER = "2.' not in pp)
    # ★ v2.9.3：deb 从"一个 all 包"改成"逐架构 5 个包"（dpkg 会拒绝架构不符的包）
    check("pack_portable.py DEB_ARCHES 五架构口径",
          'DEB_ARCHES = ["all", "amd64", "arm64", "mips64el", "loongarch64"]' in pp)
    check("pack_portable.py deb_name 按架构命名",
          'def deb_name(arch="all")' in pp and '"myme-player_%s_%s.deb" % (VER, arch)' in pp)
    check("pack_portable.py 逐架构出包并逐架构校验",
          "for arch in arches:" in pp and "verify_deb(p, arch)" in pp)
    # ★ v2.9.3：bundle 复用分支必须强制刷新 player.html
    #   否则「复用旧播放包」会把上一版播放器打进 apk/zip/deb（源码≠产物）。
    check("pack_portable.py 复用播放包时强制刷新 player.html",
          'shutil.copy2(src_ph, dst_ph)' in pp
          and '"ui", "player.html"' in pp
          and "刷新播放器" in pp)
    am = read(os.path.join(HERE, "android-player", "AndroidManifest.xml"))
    check("AndroidManifest versionName==%s" % VER, 'android:versionName="%s"' % VER in am)
    check("AndroidManifest versionCode==%d" % EXPECT_VCODE,
          ('android:versionCode="%d"' % EXPECT_VCODE) in am)
    ih = read(os.path.join(HERE, "ui", "index.html"))
    check("ui/index.html MYME_VERSION", 'const MYME_VERSION="%s"' % VER in ih)
    check("ui/player.html 版本注释", ("v%s" % VER) in read(os.path.join(HERE, "ui", "player.html")))
    # ★ v2.9.3 防卡死硬要求（用户点名的两条，源码层先卡住）：
    #   ① 每个页面都必须有「退出当前页面」按钮；② 空白处连点三下可进主菜单。
    #   主程序 index.html 在 v2.9.3 之前这两条**完全缺失**，所以这里逐条断言，别再回退。
    for needle, why in (('id="btnExit"', "主程序缺「退出当前页面」按钮"),
                        ('id="quickNav"', "主程序缺三击空白呼出的主菜单浮层"),
                        ('id="bye"', "主程序缺退出后的安全落地页"),
                        ("mymeCloseTopLayer", "主程序缺「先关最上层」的统一判定"),
                        ("mymeAnyBusy", "主程序缺「当前有没有弹层」的判定"),
                        ("addEventListener('click'", "主程序缺三击空白的点击捕获")):
        check("index.html 防卡死：%s" % why, needle in ih)
    check("index.html 三击阈值写死为 3", "hits < 3" in ih or "hits>=3" in ih or "hits >= 3" in ih)
    # player.html 两份必须字节一致（历史铁律：改一处必须同步另一处）
    a = os.path.join(HERE, "ui", "player.html")
    b = os.path.join(HERE, "android-player", "assets", "player.html")
    if os.path.isfile(b):
        check("ui/player.html 与 assets/player.html 字节一致",
              open(a, "rb").read() == open(b, "rb").read())
    # 本版关键实现必须在源码里
    ph = read(os.path.join(HERE, "ui", "player.html"))
    for needle in ("MymeNative", "applyParams", "detectParams", "pickInput", "paramSummary"):
        check("player.html 含实现「%s」" % needle, needle in ph)
    for needle, why in (('id="btnExit"', "播放器缺「退出当前页面」按钮"),
                        ("closeTopLayer", "播放器缺「先关最上层」"),
                        ("anyBusy", "播放器缺「有没有弹层」判定")):
        check("player.html 防卡死：%s" % why, needle in ph)
    mj = read(os.path.join(HERE, "android-player", "src", "com", "myme", "player", "MainActivity.java"))
    for needle in ("onShowFileChooser", "onActivityResult", "ACTION_OPEN_DOCUMENT_TREE",
                   "collectTreeFiles", "MymeNative"):
        check("MainActivity.java 含实现「%s」" % needle, needle in mj)


# ==================== Layer 2：EXE CArchive ====================
def extract_from_exe(car_cls, exe, entry):
    car = car_cls(exe)
    for cand in (entry, entry.replace("/", "\\")):
        try:
            return car.extract(cand)
        except KeyError:
            continue
    return None


def l2():
    print("\n=== Layer 2：EXE 内嵌资源（CArchive）===")
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as e:
        check("导入 PyInstaller CArchiveReader", False, repr(e))
        return
    for exe_name in ("myme.exe", "myme-play.exe"):
        exe = os.path.join(DIST, exe_name)
        if not os.path.isfile(exe):
            check("%s 存在" % exe_name, False, "缺失")
            continue
        check("%s 存在" % exe_name, True, "%.1f MB" % (os.path.getsize(exe) / 1048576))
        ih = extract_from_exe(CArchiveReader, exe, "ui/index.html")
        if ih is None:
            check("%s 内嵌 ui/index.html" % exe_name, False, "CArchive 抽离失败")
        else:
            ih = ih.decode("utf-8", "ignore")
            check("%s 内嵌 index.html 版本号 %s" % (exe_name, VER), ('MYME_VERSION="%s"' % VER) in ih)
            check("%s 内嵌 index.html 含 v%s 变更段" % (exe_name, VER), ("v%s（" % VER) in ih)
            check("%s 内嵌 index.html 含「本包标注」" % exe_name, "本包标注" in ih)
            check("%s 内嵌 index.html 含播放参数契约说明" % exe_name, "avkey" in ih)
        ph = extract_from_exe(CArchiveReader, exe, "ui/player.html")
        if ph is None:
            check("%s 内嵌 ui/player.html" % exe_name, False, "CArchive 抽离失败")
        else:
            ph = ph.decode("utf-8", "ignore")
            check("%s 内嵌 player.html 版本 %s" % (exe_name, VER), ("v%s" % VER) in ph)
            check("%s 内嵌 player.html 含自动识别实现" % exe_name, "applyParams" in ph and "MymeNative" in ph)
        cf = extract_from_exe(CArchiveReader, exe, "config.py")
        if cf is None:
            check("%s 内嵌 config.py" % exe_name, False)
        else:
            m = re.search(r'VERSION\s*=\s*"([\d.]+)"', cf.decode("utf-8", "ignore"))
            check("%s 内嵌 config.py VERSION==%s" % (exe_name, VER),
                  bool(m) and m.group(1) == VER, m.group(1) if m else "MISSING")


# ==================== Layer 3：打包产物 ====================
def _params_ok(obj):
    p = (obj or {}).get("params") or {}
    return bool(p.get("lang")) and bool(p.get("person")) and bool(p.get("avkey"))


def l3_apk():
    print("\n=== Layer 3a：安卓 APK ===")
    apk = os.path.join(DIST, "myme-%s-android-play.apk" % VER)
    if not os.path.isfile(apk):
        check("APK 存在", False, "缺失")
        return
    check("APK 存在", True, "%.1f MB" % (os.path.getsize(apk) / 1048576))
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
        ph = z.read("assets/player.html").decode("utf-8", "ignore")
        mj = json.loads(z.read("assets/manifest.json").decode("utf-8"))
        mjs = z.read("assets/manifest.js").decode("utf-8", "ignore")
        rdm = z.read("assets/README.txt").decode("utf-8", "ignore")
        dex = z.read("classes.dex")
    check("assets/player.html 含版本 %s" % VER, ("v%s" % VER) in ph)
    for needle in ("MymeNative", "applyParams", "detectParams", "pickInput", "本包无"):
        check("assets/player.html 含「%s」" % needle, needle in ph)
    check("assets/manifest.json 含 params（lang/person/avkey）", _params_ok(mj),
          json.dumps(mj.get("params") or {}, ensure_ascii=False)[:90])
    check("assets/manifest.js（内联清单）含 params", '"params"' in mjs)
    check("assets/README.txt 含「本播放包标注」", "本播放包标注" in rdm)
    # classes.dex：安卓壳必须真的实现了文件选择器（只实现 onShowCustomView 就是"点了没反应"）
    check("classes.dex 含 onShowFileChooser", b"onShowFileChooser" in dex)
    check("classes.dex 含 onActivityResult", b"onActivityResult" in dex)
    check("classes.dex 含 MymeNative（JS 桥）", b"MymeNative" in dex)
    check("classes.dex 含 DocumentsContract（SAF 目录树）", b"DocumentsContract" in dex)
    check("classes.dex 不含旧版遗留 onCancel 调用", b"onCancel" not in dex or b"CustomViewCallback" in dex)
    # aapt2 badging + apksigner
    aapt2 = os.path.join(BT, "aapt2.exe") if BT else ""
    if os.path.isfile(aapt2):
        r = subprocess.run([aapt2, "dump", "badging", apk], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        g = r.stdout or ""
        check("badging package=com.myme.player", "package: name='com.myme.player'" in g)
        check("badging versionName=%s" % VER, ("versionName='%s'" % VER) in g)
        check("badging versionCode=%d" % EXPECT_VCODE,
              ("versionCode='%d'" % EXPECT_VCODE) in g)
        check("badging 有 launchable-activity", "\nlaunchable-activity:" in ("\n" + g))
    else:
        check("aapt2 可用", False, aapt2)
    asg = os.path.join(BT, "apksigner.bat") if BT else ""
    if os.path.isfile(asg):
        r = subprocess.run([asg, "verify", "--print-certs", apk], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        check("apksigner verify 通过", r.returncode == 0, (r.stdout or r.stderr or "")[:80].replace("\n", " "))
    else:
        check("apksigner 可用", False, asg)


def l3_winzip():
    print("\n=== Layer 3b：Win 便携包 ===")
    zp = os.path.join(DIST, "myme-%s-win-x64-play.zip" % VER)
    if not os.path.isfile(zp):
        check("win 便携包存在", False, "缺失")
        return
    check("win 便携包存在", True, "%.1f MB" % (os.path.getsize(zp) / 1048576))
    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())
        get = lambda n: z.read(n).decode("utf-8", "ignore")
        check("含 myme-play.exe", "myme-play.exe" in names)
        phn = [n for n in names if n.replace("\\", "/").endswith("playback/player.html")]
        mjn = [n for n in names if n.replace("\\", "/").endswith("playback/manifest.json")]
        rdn = [n for n in names if n.replace("\\", "/").endswith("playback/README.txt")]
        check("含 playback/player.html", bool(phn))
        check("含 playback/manifest.json", bool(mjn))
        check("含 playback/README.txt", bool(rdn))
        if phn:
            ph = get(phn[0])
            check("playback/player.html 含版本 %s" % VER, ("v%s" % VER) in ph)
            check("playback/player.html 含自动识别实现", "applyParams" in ph and "MymeNative" in ph)
        if mjn:
            check("playback/manifest.json 含 params", _params_ok(json.loads(get(mjn[0]))))
        if rdn:
            check("playback/README.txt 含「本播放包标注」", "本播放包标注" in get(rdn[0]))


def l3_deb(arch="all"):
    print("\n=== Layer 3c：统信 deb [%s] ===" % arch)
    ck = lambda label, ok, note="": check("deb[%s] %s" % (arch, label), ok, note)
    deb = os.path.join(DIST, _PP.deb_name(arch))
    if not os.path.isfile(deb):
        ck("deb 存在", False, "缺失")
        return
    ck("deb 存在", True, "%.1f MB" % (os.path.getsize(deb) / 1048576))
    # 结构门禁（PAX 头 / 顶层散落 / 关键成员）——复用 pack_portable 的成熟校验
    try:
        sys.path.insert(0, HERE)
        import pack_portable as P
        err = P.verify_deb(deb, arch)
        ck("deb 结构与成员校验（verify_deb）", err == "", err)
    except Exception as e:
        ck("deb verify_deb 可执行", False, repr(e))
    # 内容级：真的把新 player.html / manifest.json 打进去了 + 统信安装铁律
    raw = open(deb, "rb").read()
    pos, data, ctrl = 8, None, None
    while pos + 60 <= len(raw):
        nm = raw[pos:pos + 16].decode("ascii", "ignore").strip()
        size = int(raw[pos + 48:pos + 58].decode("ascii", "ignore").strip() or 0)
        body = raw[pos + 60:pos + 60 + size]
        if nm.startswith("data.tar"):
            data = lzma.decompress(body)
        elif nm.startswith("control.tar"):
            ctrl = body
        pos += 60 + size + (size % 2)
    if data is None:
        ck("deb 能取出 data.tar.xz", False)
        return
    ck("deb 能取出 data.tar.xz", True)

    # ★ 统信 UOS「装不上 / 装上了打不开」专项门禁
    #   2026-10-08 实机报错复盘：
    #     An error occurred while applying changes: chmod: 无法访问'/opt/myme-player/myme-player':
    #     没有那个文件或目录 → dpkg: post-installation 脚本 子进程返回错误状态 1
    #   三条根因：①postinst 用了 set -e + 无守卫 chmod；②启动器在包内是 0644 且带 CRLF
    #   （`#!/bin/sh\r` → execve ENOENT，桌面点图标没反应）；③.desktop 用了通用图标名。
    if ctrl is not None:
        with tarfile.open(fileobj=io.BytesIO(ctrl)) as ct:
            cs = {}
            for m in ct.getmembers():
                k = m.name[2:] if m.name.startswith("./") else m.name.lstrip("/")
                cs[k] = m
            ck("deb control.tar 含 postinst/prerm/postrm",
               set(cs) >= {"control", "postinst", "prerm", "postrm"})
            pi = (ct.extractfile(cs["postinst"]).read().decode("utf-8", "ignore")
                  if "postinst" in cs else "")
            ck("deb postinst 无 set -e（行级）",
               not [l for l in pi.splitlines()
                    if l.strip() in ("set -e", "set -eu", "set -ex", "set -euo pipefail")])
            ck("deb postinst chmod 有容错（|| true）", "chmod" in pi and "|| true" in pi)
            ck("deb postinst 含完整性自检", "安装不完整" in pi)
            ck("deb postinst 无 CRLF", "\r\n" not in pi)

    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        want = {"opt/%s/player.html" % PKG, "opt/%s/manifest.json" % PKG,
                "opt/%s/serve.py" % PKG, "opt/%s/myme-player" % PKG}
        mem = set(tf.getnames())
        for w in want:
            ck("deb 含 %s" % w, any(m.endswith(w) or m == "./" + w for m in mem))
        nm2m = {}
        for m in tf.getmembers():
            k = m.name[2:] if m.name.startswith("./") else m.name.lstrip("/")
            nm2m[k] = m

        def rd(w):
            m = nm2m.get(w)
            return tf.extractfile(m).read().decode("utf-8", "ignore") if m else ""

        ph = rd("opt/%s/player.html" % PKG)
        ck("deb 内 player.html 版本 %s" % VER, ("v%s" % VER) in ph)
        ck("deb 内 player.html 含自动识别实现", "applyParams" in ph and "MymeNative" in ph)
        try:
            mj = json.loads(rd("opt/%s/manifest.json" % PKG))
            ck("deb 内 manifest.json 含 params", _params_ok(mj))
        except Exception as e:
            ck("deb 内 manifest.json 可解析", False, repr(e))

        lp = nm2m.get("opt/%s/myme-player" % PKG)
        ck("deb 启动器在包内就是 0o755（不靠 postinst 补）",
           lp is not None and bool(lp.mode & 0o111),
           oct(lp.mode & 0o777) if lp else "缺失")
        for rel in ("opt/%s/myme-player" % PKG, "opt/%s/serve.py" % PKG,
                    "usr/share/applications/%s.desktop" % PKG, "usr/bin/%s" % PKG):
            m = nm2m.get(rel)
            ck("deb %s 无 CRLF" % rel,
               m is not None and b"\r\n" not in tf.extractfile(m).read())
        ub = nm2m.get("usr/bin/%s" % PKG)
        ck("deb usr/bin/%s 在包内 0o755" % PKG, ub is not None and bool(ub.mode & 0o111))
        ck("deb 含 opt/%s/icon.png" % PKG, nm2m.get("opt/%s/icon.png" % PKG) is not None)
        det = rd("usr/share/applications/%s.desktop" % PKG)
        ck("deb .desktop Exec 绝对路径", ("Exec=/usr/bin/%s" % PKG) in det)
        ck("deb .desktop Icon 绝对路径（禁通用图标名）",
           ("Icon=/opt/%s/icon.png" % PKG) in det)
        ck("deb 无中文文件名（LANG=C 机器上不乱码）",
           not [m.name for m in tf.getmembers() if any(ord(c) > 127 for c in m.name)])


def l3_others():
    print("\n=== Layer 3d：其余产物 ===")
    for fn, minsz in (("myme-%s-win-x64-full.msi" % VER, 300 * 1048576),
                      ("myme-%s-runtime.7z" % VER, 300 * 1048576),
                      ("myme-setup.exe", 10 * 1048576)):
        p = os.path.join(DIST, fn)
        ok = os.path.isfile(p) and os.path.getsize(p) >= minsz
        check("产物 %s" % fn, ok,
              ("%.1f MB" % (os.path.getsize(p) / 1048576)) if os.path.isfile(p) else "缺失")


def main():
    print("=" * 62)
    print("myme v%s 抽包门禁（Layer1 源码 / Layer2 EXE / Layer3 产物）" % VER)
    print("=" * 62)
    l1()
    l2()
    l3_apk()
    l3_winzip()
    for _a in _PP.DEB_ARCHES:
        l3_deb(_a)
    l3_others()
    print("\n" + "=" * 62)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败清单：")
        for f in FAIL:
            print("  ✗", f)
        print("结论：❌ 门禁不通过")
        return 1
    print("结论：✅ ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
