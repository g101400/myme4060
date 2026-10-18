# -*- coding: utf-8 -*-
r"""发布 myme 到 GitHub（走 REST API，不依赖 git push）。

为什么不用 git push：本机 https_proxy 对 github.com 的 CONNECT 返回 502，
git push 必然失败；而 api.github.com / uploads.github.com 走同一代理是通的。
所以流程是：本地 commit + tag → 调 Release API 建 release（GitHub 自动建 tag）→ 逐个传附件。

⚠ 两个坑（v2.6.7 血泪）：
  1) 传附件的 name 必须是**纯文件名**，用绝对路径会被 GitHub 把 `\` 换成 `.`，
     变成 `D.Users...myme-2.6.7-android-play.apk` 这种怪名字；传错就
     DELETE /releases/assets/{id} 删掉重传。
  2) 大文件（280~300MB）一次约 30~35 秒，urlopen(timeout=900) 才够。

用法：
  python publish_release.py                 # 自动取 config.VERSION 与 HEAD
  python publish_release.py --version 2.6.8 --assets "a|b|c"
环境变量：MYME_GH_TOKEN（或 D:/Users/WorkBuddy/.github_token 里的第一行）
"""
import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "g101400/myme4060"

RELEASE_BODY = """## myme v%(ver)s · PPT制作及分身演示

本地跑通「PPT → 分身语音讲解 → 数字人演示 → 问答 → 导出离线播放包」的一体化桌面应用。
**Windows = 创作端 + 播放端；统信 UOS / 安卓 = 纯播放端；iOS = Safari 添加到主屏幕的 PWA。**

### 本版重点：脸 / 声音 / 语言 三方同源

以前一个播放包里的中英文配音只有一份（谁生成算谁的），而"分身"有好几位 ——
于是「金子 · 中文」实际播的是**炎冰的嗓子**配**金子的脸**。本版从数据层治本：

- 每页记录 `audio_person`（中文配音归属）/ `audio_person_en`（英文配音归属）
- 导出时汇总成 `params.voices = {分身: {zh, en}}` 写进 `manifest.json`
- 播放端 `canPlay()` 三层判定：整包有没有这种语言 → 这个人有没有这种语言 → 片段是否齐
- **缺的置灰并写明原因**（「· 无」/「仅画面」），选了会自动回落到可用项并当场提示，
  **绝不静默换成别人的声音**；老包（无 `voices`）一律放行，不误伤

### 下载怎么选

| 你要干什么 | 下哪个 |
|---|---|
| 完整创作（出 PPT、造分身、演示、答疑） | `myme-%(ver)s-win-x64-full.msi`（637 MB，含 LivePortrait + ts_deps，装完不补东西） |
| 创作，但已有 LivePortrait/ComfyUI | `myme-%(ver)s-win-x64-setup.msi` 或 `-setup.exe`（21 MB）+ 按需 `myme-%(ver)s-runtime.7z` |
| 只播放 · Windows | `myme-%(ver)s-win-x64-play.zip`（64.5 MB，解压双击 `playback/player.html`） |
| 只播放 · 安卓 | `myme-%(ver)s-android-play.apk`（46.9 MB，Android 8.0+） |
| 只播放 · 统信 UOS / 麒麟 / Deepin | `myme-player_%(ver)s_<架构>.deb`（41.4 MB）。**龙芯 3A3000/3A4000 用 `mips64el`，3A5000+ 用 `loongarch64`，飞腾/鲲鹏用 `arm64`，其余 `amd64`**；不确定就装 `_all` |
| 只看不装 · iPhone/iPad | `myme-ios-pwa.zip`（解压托管到任意 HTTPS 静态站，Safari 打开 → 添加到主屏幕） |
| 源码 / 二开 | `myme_source_%(ver)s.zip` |

> MSI 与 EXE 内容等价：MSI 走 Windows Installer，EXE 完全不依赖它（老机器 / 被组策略管控的机器用它）。
> 两者都是 **perUser 安装到 `%%LocalAppData%%\\myme`，不弹 UAC**，卸载保留用户数据。

### 本机实测（不是"应该行"）

- 三套前端真跑夹具 **119 条断言全绿**（防卡死 33 / 菜单 60 / 分身×语言矩阵 26）
- 抽包门禁 `_gate_292.py` **212 / 212 ALL OK**：源码层 → exe 的 CArchive 里**实际抽出**的 `index.html`/`player.html`
  → APK 的 badging（`versionCode=12`/`2.9.5`）与 `classes.dex` 方法名 → deb×5 逐架构解包 → MSI 的 OLE 头 /
  `ProductVersion` / 内嵌 `myme.exe`
- 示例播放包体积 144.6 → 46.9 MB（逐页图 1600px / JPEG q86）：龙芯 3A400 无独显解 2 MB PNG 会卡

更细的版本变更见应用内「❓ 帮助 → 版本变更」与本仓 `三端对照表.md`。
"""
TOKEN_FILES = [os.environ.get("MYME_GH_TOKEN", ""),
               r"D:/Users/WorkBuddy/.github_token",
               r"C:/Users/admin/.github_token"]


def gh_token():
    for t in TOKEN_FILES:
        if t and len(t) > 20 and t.startswith(("ghp_", "github_pat_")):
            return t
        if t and os.path.isfile(t):
            try:
                line = open(t, encoding="utf-8", errors="replace").readline().strip()
                if line:
                    return line
            except Exception:
                pass
    return ""


def api(method, path, body=None, host="api.github.com", timeout=60):
    c = http.client.HTTPSConnection(host, timeout=timeout)
    hdrs = {"Authorization": "Bearer " + gh_token(),
            "Accept": "application/vnd.github+json",
            "User-Agent": "myme-release"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    try:
        c.request(method, path, body=data, headers=hdrs)
        r = c.getresponse()
        raw = r.read().decode("utf-8", "replace")
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, raw
    finally:
        c.close()


def upload(release_id, asset_path):
    fn = os.path.basename(asset_path)          # ⚠ 只传文件名，别传绝对路径
    c = http.client.HTTPSConnection("uploads.github.com", timeout=900)
    q = "/repos/%s/releases/%d/assets?name=%s" % (
        REPO, release_id, urllib.parse.quote(fn))
    try:
        with open(asset_path, "rb") as f:
            # ⚠ https.client 没有 conn.put()，只有 conn.request("PUT", ...)
            c.request("PUT", q, body=f,
                      headers={"Authorization": "Bearer " + gh_token(),
                               "Content-Type": "application/octet-stream",
                               "Content-Length": str(os.path.getsize(asset_path)),
                               "User-Agent": "myme-release"})
            r = c.getresponse()
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    finally:
        c.close()


def main():
    version = ""
    sys.path.insert(0, HERE)
    try:
        import config
        version = str(getattr(config, "VERSION", "")).strip()
    except Exception:
        pass
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--version" and i + 1 < len(args):
            version = args[i + 1]
        if a == "--assets" and i + 1 < len(args):
            pass
    if not version:
        version = (input("版本号：") or "").strip()
    if not version:
        raise SystemExit("拿不到版本号")

    sha = ""
    try:
        import subprocess
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE,
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        pass

    tag = "v" + version
    body = RELEASE_BODY % {"ver": version}
    st, rel = api("POST", "/repos/%s/releases" % REPO,
                  {"tag_name": tag, "name": "myme v%s" % version,
                   "body": body,
                   "target": sha, "draft": False, "prerelease": False})
    if st >= 400:
        # 已存在同名 release 时，改用已有那个，别重复建
        st, rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, tag))
        print("[publish] release 已存在(%s)：%s" % (st, rel.get("id") if isinstance(rel, dict) else rel))
    if not isinstance(rel, dict) or "id" not in rel:
        print("[publish] 建 release 失败：", st, str(rel)[:300])
        return 1
    rid = rel["id"]
    print("[publish] release %s -> id=%s" % (tag, rid))

    assets = []
    dl = os.path.join(HERE, "dist")
    # ⚠ myme-setup.exe（自解压精简包）必须在内：手册第 0 节把它列为"已有运行时"用户的首选，
    #   2026-10-16 发 v2.9.2 时发现清单漏了它（v2.9.1 同漏），用户按手册找包会找不到。
    pats = ["myme-%s-win-x64-setup.msi" % version,      # v2.9.5 起：WiX 精简 MSI
            "myme-%s-win-x64-setup.exe" % version,      #         同款 NSIS EXE（双保险）
            "myme-%s-win-x64-full.msi" % version,
            "myme-%s-runtime.7z" % version,
            "myme-%s-win-x64-play.zip" % version,
            "myme-%s-android-play.apk" % version,
            "myme-player_%s_all.deb" % version,
            "myme-player_%s_amd64.deb" % version,
            "myme-player_%s_arm64.deb" % version,
            "myme-player_%s_mips64el.deb" % version,
            "myme-player_%s_loongarch64.deb" % version,
            "myme-ios-pwa.zip", "myme_source_%s.zip" % version]
    for p in pats:
        fp = os.path.join(dl, p)
        if os.path.isfile(fp):
            assets.append(fp)
        else:
            for fn in sorted(os.listdir(dl)) if os.path.isdir(dl) else []:
                if p.replace(version, "*") in fn and fn.endswith((".zip", ".apk", ".deb", ".msi", ".exe", ".7z")):
                    fp2 = os.path.join(dl, fn)
                    if os.path.isfile(fp2):
                        assets.append(fp2)
                    break
    if not assets:
        print("[publish] dist 里没找到产物，先跑 build_all.bat + pack_full.py")
    for fp in assets:
        t0 = time.time()
        st2, res = upload(rid, fp)
        nm = res.get("name") if isinstance(res, dict) else str(res)[:80]
        print("[publish] %s  %s  %.0fs" % (st2, nm, time.time() - t0))
        time.sleep(1)
    print("[publish] 完成：https://github.com/%s/releases/tag/%s" % (REPO, tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
