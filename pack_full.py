# -*- coding: utf-8 -*-
"""
myme 完整安装包打包（v2.6）
=================================
产物（全部落在 dist/）：
  1. myme-<ver>-win-x64-full.msi   —— 完整安装包：主程序 + LivePortrait(682MB) + ts_deps(141MB) + 声纹库
                                      用 WiX v7（dotnet 全局工具 wix）生成，EmbedCab 单文件 MSI
  2. myme-<ver>-runtime.7z         —— 绿色伴随目录：只有重依赖（LivePortrait + ts_deps + voice）
                                      给"先装精简包、后补运行时"或换机部署用，解压后到
                                      「设置 → 🗂 运行时目录配置」登记目录即可
  3. 安装说明.txt                  —— 随包说明：放在哪个目录 / 怎么登记 / 常见坑

为什么 MSI 能装下 682MB：
  · WiX v4/v7 的 MediaTemplate 会把全部文件压进内嵌 cab，MSI 单文件即可分发；
  · 单 cab 上限 2GB，本包 ~860MB 原始 / ~450MB 压缩后，安全；
  · 与 exe 自解压相比：MSI 有标准卸载入口、开始菜单/桌面快捷方式、MajorUpgrade 覆盖升级。

用法：
  python pack_full.py            # 全量（MSI + 绿色伴随包 + 说明）
  python pack_full.py nomzsi     # 跳过 MSI，只出绿色伴随包
"""
import os
import re
import sys
import json
import time
import shutil
import hashlib
import subprocess
import xml.sax.saxutils as X

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
VER = "2.6.3"
PRODUCT = "PPT制作及分身演示"
MANUF = "小七"
# ⚠ 升级码（UpgradeCode）：决定"新版本能否覆盖旧版本"。
#   旧版（≤2.6.0）是 perMachine（装 Program Files），曾导致 Win10/11「错误代码 2503/2502」；
#   2.6.1 起改为 perUser（装 LocalAppData，见下方 Package/Scope）。
#   但"perMachine 旧版"与"perUser 新版"升级码相同 → 新版安装时会尝试自动卸载旧版，
#   而卸载 perMachine 需要管理员，普通用户下即报 2503(无法访问)→2502(意外错误)。
#   解决：换新升级码，让 perUser 新版成为"独立产品"，不再去碰旧的 perMachine 安装，
#   普通双击即可装完。装过旧版的用户在「设置→应用」里手动卸载旧版即可（两版会并存）。
UPGRADE_CODE = "{8F3B2C1A-4D5E-4A6B-9C7D-0E1F2A3B4C5D}"

# wix 绑定中文路径会挂起 → 暂存目录必须用纯 ASCII 路径
STAGE = os.path.join("C:/tmp/myme_pkg", "stage_full")
TMPWXS = os.path.join("C:/tmp/myme_pkg", "myme_full.wxs")
SEVENZ = r"C:\Program Files\7-Zip\7z.exe"

SKIP_DIRS = {".git", ".vscode", ".idea", "__pycache__", "node_modules",
             "build", "dist", "dist_pkg", ".venv", "kb_data", "projects"}
SKIP_EXT = (".pyc", ".pyo", ".tmp", ".log", ".bak", ".msi", ".wxs", ".7z", ".zip")


def log(*a):
    print("[pack_full]", *a, flush=True)


def _ignore(d, names):
    out = set()
    for n in names:
        full = os.path.join(d, n)
        if os.path.isdir(full):
            if n in SKIP_DIRS or n.startswith("."):
                out.add(n)
        elif n.lower().endswith(SKIP_EXT):
            out.add(n)
    return out


def stage():
    """把要进包的东西拷到 ASCII 暂存目录。"""
    # ⚠ 本环境对批量删除有确认垫片（>50 文件即拦），故只覆盖写、不删旧暂存
    os.makedirs(STAGE, exist_ok=True)

    exe = os.path.join(DIST, "myme.exe")
    if not os.path.isfile(exe):
        raise SystemExit("❌ 缺主程序：" + exe + "（请先构建 dist/myme.exe）")
    shutil.copy2(exe, os.path.join(STAGE, "myme.exe"))
    log("主程序", os.path.getsize(exe), "B")

    for name, src in (("LivePortrait", os.path.join(HERE, "LivePortrait")),
                      ("ts_deps", os.path.join(HERE, "ts_deps")),
                      ("voice", os.path.join(HERE, "voice"))):
        if not os.path.isdir(src):
            log("⚠ 跳过（不存在）：", src)
            continue
        t0 = time.time()
        shutil.copytree(src, os.path.join(STAGE, name), ignore=_ignore, dirs_exist_ok=True)
        n = sum(len(f) for _, _, f in os.walk(os.path.join(STAGE, name)))
        log("%s: %d 个文件，%.1fs" % (name, n, time.time() - t0))

    # 随包说明（安装后躺在安装目录里，方便用户自己排查）
    with open(os.path.join(STAGE, "安装说明.txt"), "w", encoding="utf-8") as f:
        f.write(INSTALL_TXT)
    return STAGE


INSTALL_TXT = """myme 数字分身演示系统 v%s —— 安装说明
================================================

【装完在哪】
  默认装到 C:\\Program Files\\myme\\
    myme.exe            主程序（双击即可，会自动打开浏览器界面）
    LivePortrait\\       口型驱动模型（682MB，已随本包安装）
    ts_deps\\            语音合成依赖隔离包（141MB，已随本包安装）
    voice\\              声纹样本（示例音色）

【哪些还要自己准备】
  · ComfyUI（含内置 Python 与 Qwen3-TTS 声纹克隆模型）：体积太大不随包分发。
    装好后到软件里「设置 → 🗂 运行时目录配置」登记 ComfyUI 根目录，点「自动探测」多半能直接找到。
  · ffmpeg：choco install ffmpeg，或在同一个界面指定 ffmpeg.exe。
  · 大模型（可选）：Ollama 或 OpenAI 兼容接口，在「设置 → 🤖 大模型接入」里新增并点「测试连通」。
  缺什么、影响什么、去哪装，都在「设置 → 🧩 运行环境自检」里逐条写明。

【换机 / 只想要主程序】
  用 myme-%s-runtime.7z（绿色伴随目录）即可：解压到任意盘（建议 D:\\myme-runtime\\），
  再到「设置 → 🗂 运行时目录配置」登记 LivePortrait 与 ts_deps 两个目录，保存后即时生效，不用重装。

【常见坑】
  1. 首次启动慢：onefile 首次运行要解压 62MB 运行时，约 10~30 秒属正常。
  2. 提示"语音合成失败"：九成是 ComfyUI 根目录没登记对，或它下面没有 models\\qwen-tts\\Qwen3-TTS-12Hz-1.7B-Base。
  3. 演示只剩静态图没有口型：LivePortrait 目录没登记/不在位，到运行时目录配置里核对。
  4. 数据存在 %%LOCALAPPDATA%%\\myme\\projects\\ 下，卸载不会带走；要备份请用「⑧ 资源管理 → 导出」。
""" % (VER, VER)


# ---------------- WiX v7 ----------------
def find_wix():
    for base in (os.path.join(os.environ.get("USERPROFILE", ""), ".dotnet", "tools"),
                 "C:/Users/admin/.dotnet/tools", os.path.expanduser("~/.dotnet/tools")):
        c = os.path.join(base, "wix.exe")
        if os.path.isfile(c):
            return c
    for p in os.environ.get("PATH", "").split(os.pathsep):
        c = os.path.join(p, "wix.exe")
        if os.path.isfile(c):
            return c
    raise SystemExit("❌ 未找到 wix（请先 dotnet tool install --global wix）")


def hid(prefix, s):
    return prefix + hashlib.md5(s.encode("utf-8")).hexdigest()[:20]


def build_tree(files_rel):
    tree = {"dirs": {}, "files": []}
    for rel in files_rel:
        parts = rel.split("/")
        node = tree
        for p in parts[:-1]:
            node = node["dirs"].setdefault(p, {"dirs": {}, "files": []})
        node["files"].append(parts[-1])
    return tree


def render_node(node, prefix, comp_ids, out, depth):
    ind = "  " * depth
    for fname in node["files"]:
        full = (prefix + "/" + fname) if prefix else fname
        cid = hid("C_", full)
        fid = hid("F_", full)
        comp_ids.append(cid)
        out.append('%s<Component Id="%s">' % (ind, cid))
        out.append('%s  <File Id="%s" Source="%s" />' % (ind, fid, X.escape(full)))
        out.append("%s</Component>" % ind)
    for dname, child in sorted(node["dirs"].items()):
        cp = (prefix + "/" + dname) if prefix else dname
        out.append('%s<Directory Id="%s" Name="%s">' % (ind, hid("D_", cp), X.escape(dname)))
        render_node(child, cp, comp_ids, out, depth + 1)
        out.append("%s</Directory>" % ind)


def gen_wxs(stage_dir, ico):
    files_rel = []
    for dp, dirs, fns in os.walk(stage_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if fn.lower().endswith(SKIP_EXT):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), stage_dir).replace(os.sep, "/")
            files_rel.append(rel)
    files_rel.sort()
    log("暂存文件数：", len(files_rel))
    tree = build_tree(files_rel)
    comp_ids = []
    body = []
    render_node(tree, "", comp_ids, body, depth=4)
    app_esc = X.escape(PRODUCT)
    # ⚠ 安装范围必须 perUser + 装进 LocalAppData：这是 Win10/11「错误代码 2503/2502」
    #   的唯一根因。perMachine + Program Files 需要 admin 提权，而 msiexec 的
    #   自我提权在很多机器上被 UAC/策略拦掉，用户就看到 2503(无法访问)→2502(意外错误)。
    #   perUser 不触发提权、不需要管理员，普通双击就能装完，也免去卸载时的权限问题。
    #   与之配套：目录用 LocalAppDataFolder、注册表只写 HKCU（本来就是）。
    L = ['<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
         '  <Package Name="%s" Language="2052" Version="%s" Manufacturer="%s" UpgradeCode="%s" '
         'InstallerVersion="200" Scope="perUser">' % (app_esc, VER, MANUF, UPGRADE_CODE),
         '    <MajorUpgrade DowngradeErrorMessage="已安装更高版本，请先卸载。" />',
         '    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />',
         '    <Property Id="ARPPRODUCTICON" Value="AppIcon" />',
         '    <Feature Id="ProductFeature" Title="主程序与运行时" Level="1">',
         '      <ComponentGroupRef Id="MainComponents" />',
         '      <ComponentRef Id="StartMenuShortcut" />',
         '      <ComponentRef Id="DesktopShortcut" />',
         '    </Feature>',
         '    <StandardDirectory Id="LocalAppDataFolder">',
         '      <Directory Id="INSTALLFOLDER" Name="myme">']
    L += body
    L += ['      </Directory>',
          '    </StandardDirectory>',
          '    <StandardDirectory Id="ProgramMenuFolder">',
          '      <Directory Id="AppMenuFolder" Name="%s" />' % app_esc,
          '    </StandardDirectory>',
          '    <StandardDirectory Id="DesktopFolder" />',
          '    <Component Id="StartMenuShortcut" Directory="AppMenuFolder">',
          '      <Shortcut Id="SM" Name="%s" Target="[INSTALLFOLDER]myme.exe" Icon="AppIcon" IconIndex="0" />' % app_esc,
          '      <RemoveFolder Id="RM" Directory="AppMenuFolder" On="uninstall" />',
          '      <RegistryValue Root="HKCU" Key="Software\\myme" Name="installed" Type="integer" Value="1" KeyPath="yes" />',
          '    </Component>',
          '    <Component Id="DesktopShortcut" Directory="DesktopFolder">',
          '      <Shortcut Id="DT" Name="%s" Target="[INSTALLFOLDER]myme.exe" Icon="AppIcon" IconIndex="0" />' % app_esc,
          '      <RegistryValue Root="HKCU" Key="Software\\myme" Name="desktop" Type="integer" Value="1" KeyPath="yes" />',
          '    </Component>',
          '    <Icon Id="AppIcon" SourceFile="%s" />' % X.escape(ico),
          '    <ComponentGroup Id="MainComponents">']
    for cid in comp_ids:
        L.append('      <ComponentRef Id="%s" />' % cid)
    L += ['    </ComponentGroup>', '  </Package>', '</Wix>']
    return "\n".join(L), len(comp_ids)


def make_ico():
    """用 PNG 生成 .ico（WiX 的 Icon 只吃 .ico）。"""
    dst = os.path.join("C:/tmp/myme_pkg", "myme.ico")
    if os.path.isfile(dst):
        return dst
    try:
        from PIL import Image
        src = os.path.join(HERE, "icon-512.png")
        im = Image.open(src).convert("RGBA").resize((256, 256))
        im.save(dst, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        return dst
    except Exception as e:
        log("⚠ 生成 ico 失败（改用无图标快捷方式）：", e)
        return ""


def _locate_msi(d, since_ts):
    """按修改时间捞本次构建产出的 .msi（兼容被落成 8.3 短名的情况）。"""
    best, best_t = "", 0
    try:
        for fn in os.listdir(d):
            if not fn.lower().endswith(".msi"):
                continue
            p = os.path.join(d, fn)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt >= since_ts - 5 and mt > best_t:
                best, best_t = p, mt
    except OSError:
        pass
    return best


def build_msi():
    ico = make_ico()
    wxs, n = gen_wxs(STAGE, ico or "")
    if not ico:
        wxs = re.sub(r'\s*<Property Id="ARPPRODUCTICON"[^/]*/>', "", wxs)
        wxs = wxs.replace(' Icon="AppIcon" IconIndex="0"', "")
        wxs = re.sub(r'\s*<Icon Id="AppIcon" SourceFile="" />', "", wxs)
    with open(TMPWXS, "w", encoding="utf-8") as f:
        f.write(wxs)
    out = os.path.join(DIST, "myme-%s-win-x64-full.msi" % VER)
    # ⚠ 实测坑：wix 直接 -out 到 D: 盘会静默失败（进程退出码 0 但产物不存在）。
    #   改为先输出到同盘临时目录，再用流式拷贝搬到 dist（可靠且可验证大小）。
    tmp_out = "C:/tmp/myme_pkg/myme_full.msi"
    # 不引 -ext：本机没装 WixToolset.UI.wixext，且本 wxs 不用任何 UI 扩展元素
    cmd = [find_wix(), "build", TMPWXS, "-out", tmp_out, "-arch", "x64"]
    log("wix build 开始（约 2~5 分钟，3042 个文件）：", " ".join(cmd[:3]))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=STAGE, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    got = tmp_out if os.path.isfile(tmp_out) else _locate_msi("C:/tmp/myme_pkg", t0)
    if r.returncode != 0 or not got:
        log("❌ wix build 失败 rc=%s\n" % r.returncode,
            (r.stdout or "")[-3000:], "\n", (r.stderr or "")[-2000:])
        return ""
    if got != tmp_out:
        # ⚠ 实测坑：本机 wix 写出的文件会落成 8.3 短名（myme_full.msi → MYME_F~1.MSI），
        #   直接按原路径找不到。这里按"本次构建之后新出现的 .msi"把它捞出来。
        log("⚠ wix 产物落在 8.3 短名：%s（已按此搬运）" % os.path.basename(got))
    with open(got, "rb") as f, open(out, "wb") as g:
        while True:
            buf = f.read(4 * 1024 * 1024)
            if not buf:
                break
            g.write(buf)
    if not os.path.isfile(out) or os.path.getsize(out) != os.path.getsize(got):
        log("❌ MSI 搬运失败")
        return ""
    log("✅ MSI 完成：%s（%.1f MB，%.1fs，%d 组件）" %
        (out, os.path.getsize(out) / 1048576, time.time() - t0, n))
    return out


def build_runtime_7z():
    """绿色伴随目录：只打包重依赖，给精简安装 / 换机补装用。"""
    out = os.path.join(DIST, "myme-%s-runtime.7z" % VER)
    tmp = os.path.join("C:/tmp/myme_pkg", "runtime")
    os.makedirs(tmp, exist_ok=True)
    for name in ("LivePortrait", "ts_deps"):
        src = os.path.join(HERE, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(tmp, name), ignore=_ignore, dirs_exist_ok=True)
    with open(os.path.join(tmp, "放到哪个目录.txt"), "w", encoding="utf-8") as f:
        f.write(RUNTIME_TXT)
    cmd = [SEVENZ, "a", "-t7z", out, "-r", os.path.join(tmp, "*"),
           "-mx=3", "-mmt=on", "-bso0", "-bsp0"]
    log("7z 打包绿色伴随目录…（约 2~5 分钟）")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0 or not os.path.isfile(out):
        log("❌ 7z 失败：", (r.stdout or "")[-1500:], (r.stderr or "")[-800:])
        return ""
    log("✅ 绿色伴随包：%s（%.1f MB，%.1fs）" % (out, os.path.getsize(out) / 1048576, time.time() - t0))
    return out


RUNTIME_TXT = """myme 运行时绿色伴随目录 v%s
=================================

【这两个目录放哪】
  解压到任意盘的根目录下即可，建议：
      D:\\myme-runtime\\LivePortrait\\
      D:\\myme-runtime\\ts_deps\\
  ⚠ 不要改名，也不要把它们塞进 Program Files（权限麻烦）。

【解压后怎么做】
  打开 myme → 「设置 → 🗂 运行时目录配置」：
    · LivePortrait 目录   → 选到 ...\\LivePortrait 这一层（里面有 src\\ 与 pretrained_weights\\）
    · ts_deps 目录        → 选到 ...\\ts_deps 这一层（里面有 transformers\\）
  点「🔍 自动探测」通常能一次找齐；保存后即时生效，不用重启软件。

【各自是干什么的】
  · LivePortrait（607MB 权重）—— 数字人的口型驱动。没有它，演示画面是静态图 + 配音。
  · ts_deps（141MB）—— 锁住 transformers 4.57.3。没有它，语音合成会报张量形状不匹配而失败。

【校验】
  保存后到「设置 → 🧩 运行环境自检」看是否全绿；任一项黄色都能直接点「去设置」跳过来改。
""" % VER


def main():
    args = sys.argv[1:]
    os.makedirs("C:/tmp/myme_pkg", exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    t0 = time.time()
    stage()
    outs = []
    if "nomsi" not in args:
        outs.append(build_msi())
    if "noruntime" not in args:
        outs.append(build_runtime_7z())
    log("全部完成，用时 %.1fs" % (time.time() - t0))
    for o in outs:
        if o:
            print("  →", o)


if __name__ == "__main__":
    main()
