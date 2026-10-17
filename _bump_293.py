# -*- coding: utf-8 -*-
"""v2.9.2 → v2.9.3 版本号同步（八处 + 变更记录 + 帮助清单）。

纪律：每个替换都带出现次数断言；改不到就报错退出，绝不"静默改一半"。
v2.9.3 内容：统信 deb 装了打不开的根因根治（curl 探测 + 代理伪造就绪）、
逐架构出 deb、界面精修、主程序补齐「退出当前页面 + 三击空白呼主菜单」。
"""
import io
import sys

OK = []


def rep(path, old, new, cnt=1, where=""):
    src = io.open(path, encoding="utf-8").read()
    n = src.count(old)
    if n != cnt:
        raise SystemExit("❌ %s：出现 %d 次（期望 %d）: %r" % (path, n, cnt, old[:80]))
    io.open(path, "w", encoding="utf-8", newline="\n").write(src.replace(old, new))
    OK.append("%-34s %s" % (where or path, old[:46].replace("\n", "⏎")))


OLD, NEW = "2.9.2", "2.9.3"

# ---------- 1) config.py：版本唯一真相源 ----------
rep("config.py", 'VERSION = "%s"' % OLD, 'VERSION = "%s"' % NEW, 1, "config.VERSION")

# ---------- 2) version_info.txt：exe/msi 版本资源 ----------
rep("version_info.txt", "(2, 9, 2, 0)", "(2, 9, 3, 0)", 2, "version_info 元组")
rep("version_info.txt", "'%s.0'" % OLD, "'%s.0'" % NEW, 2, "version_info 字符串")

# ---------- 3) 安卓：versionCode +1 / versionName ----------
rep("android-player/AndroidManifest.xml", 'android:versionCode="9"',
    'android:versionCode="10"', 1, "AndroidManifest versionCode")
rep("android-player/AndroidManifest.xml", 'android:versionName="%s"' % OLD,
    'android:versionName="%s"' % NEW, 1, "AndroidManifest versionName")

# ---------- 4) WiX：MSI 版本 ----------
rep("myme.wxs", 'Version="%s"' % OLD, 'Version="%s"' % NEW, 1, "myme.wxs Version")

# ---------- 5) 门禁：EXPECT_VCODE + 自身标题 ----------
rep("_gate_292.py", "EXPECT_VCODE = 9", "EXPECT_VCODE = 10", 1, "门禁 EXPECT_VCODE")
rep("_gate_292.py", '"""myme v%s 抽包门禁' % OLD, '"""myme v%s 抽包门禁' % NEW, 1, "门禁标题")
rep("_gate_292.py", "APK   —— assets/player.html 含 v%s" % OLD,
    "APK   —— assets/player.html 含 v%s" % NEW, 1, "门禁 Layer3 说明")
rep("_gate_292.py", "deb   —— data.tar.xz 内 opt/myme-player/player.html 含 v%s" % OLD,
    "deb   —— 逐架构 data.tar.xz 内 opt/myme-player/player.html 含 v%s" % NEW, 1, "门禁 deb 说明")

# ---------- 6) ui/index.html：MYME_VERSION + 帮助清单 + 变更记录 ----------
P = "ui/index.html"
rep(P, 'const MYME_VERSION="%s";' % OLD, 'const MYME_VERSION="%s";' % NEW, 1, "index MYME_VERSION")
for a, b in (("myme-%s-win-x64-full.msi" % OLD, "myme-%s-win-x64-full.msi" % NEW),
             ("myme-setup.exe + myme-%s-runtime.7z" % OLD, "myme-setup.exe + myme-%s-runtime.7z" % NEW),
             ("myme-%s-win-x64-play.zip" % OLD, "myme-%s-win-x64-play.zip" % NEW),
             ("myme-%s-android-play.apk" % OLD, "myme-%s-android-play.apk" % NEW)):
    rep(P, a, b, 1, "帮助清单 %s" % b.split("-")[-1])

rep(P,
    '<li><span class="fk">myme-player_%s_all.deb</span><span class="fv">统信/麒麟/Deepin，架构 all；'
    '本地 python3 服务 + 浏览器，不装 PyQt6</span></li>' % OLD,
    '<li><span class="fk">myme-player_%s_&lt;arch&gt;.deb</span><span class="fv">'
    '统信/麒麟/Deepin，<b>五架构分别出包</b>：all / amd64 / arm64 / mips64el / loongarch64。'
    '龙芯 3A3000·3A4000 取 <b>mips64el</b>、3A5000 及以上取 <b>loongarch64</b>、'
    '飞腾 FT2000·鲲鹏取 <b>arm64</b>、其余（兆芯/海光/Intel/AMD）取 <b>amd64</b>；'
    '本地 python3 服务 + 浏览器，不装 PyQt6</span></li>' % NEW,
    1, "帮助清单 deb 逐架构")

BLOCK = """  <h4 style="margin:0 0 6px">v2.9.3（10-16 统信「装了打不开」根治 · 界面精修 · 防卡死补齐 · 逐架构 deb）</h4>
  <ul class="flist">
    <li><b>统信 deb 装了打不开（根因定案并根治）</b>：旧启动器用 <code>curl</code> 判断"服务是否已就绪"，
        而 curl 对<b>任何</b> HTTP 应答都返回 0 —— 系统配了 <code>http_proxy</code>（内网 UOS 极常见）时，
        访问 127.0.0.1 会走代理、代理回 502，于是被误判成"服务已在运行"，
        <b>启动器干脆不启动服务</b>，浏览器打开死端口 → 白屏（用户报的"打不开"）。
        现在启动逻辑整体搬进 <code>launch.py</code>：就绪探针用 python <b>显式空代理</b> + 专属 magic token
        （<code>/__myme_ping__</code>），后台脱离用 <code>start_new_session</code>（不再依赖 setsid 命令），
        失败一定可见（中文诊断页 + 桌面通知 + <code>/tmp/myme-player.log</code> + 状态文件），
        并把 127.0.0.1/localhost/::1 写进 <code>no_proxy</code>；
        同时修掉"机器上没有 curl / wget 就假失败 22 秒""没有任何报错、点图标毫无反应"两条隐患。</li>
    <li><b>逐架构出 deb</b>：由原来一个 <code>_all.deb</code> 改为按固化架构表分别出包
        （all / amd64 / arm64 / mips64el / loongarch64），control 的 Architecture 与文件名严格一致，
        dpkg 不再因架构不符而挑不着包；门禁对 5 个包逐个解包断言。</li>
    <li><b>界面精修（三端同一套设计语言）</b>：品牌栏统一为「圆角渐变 logo + 主标题 + 版本徽标 + 副标题」，
        主色青绿 <code>#0f766e</code> / 辅色 <code>#14b8a6</code> / 正文 <code>#0f172a</code>，
        字体栈、圆角、投影、按钮三态（主/次/危险）全部对齐 —— 主程序与统信、安卓播放器的
        标题、菜单、按钮不再各长各样。</li>
    <li><b>防卡死补齐（用户点名的两条）</b>：主程序此前<b>没有</b>「退出当前页面」按钮、也没有三击空白的快捷入口，
        现已补齐并实跑验证：右上角常驻「⏏ 退出当前页面」（停一切播放 + 关所有弹层 + 退全屏 → 安全落地页）；
        空白处快速连点三下 → 有弹层先关最上层、一个弹层都没有时才弹出「🧭 主菜单」；
        Esc 逐层退出。三端语义完全一致（退出页 → 主菜单 → 弹窗 → 菜单）。</li>
    <li><b>功能与菜单只增不减</b>：原有十三个标签、全部一级菜单与子菜单项一项不少，只调整视觉与顺序。</li>
  </ul>
"""
rep(P, '  <h4 style="margin:0 0 6px">v%s（10-15 交接线合并' % OLD,
    BLOCK + '  <h4 style="margin:0 0 6px">v%s（10-15 交接线合并' % OLD, 1, "变更记录 v2.9.3 置顶")

print("v%s → v%s 版本同步完成，共 %d 处：" % (OLD, NEW, len(OK)))
for line in OK:
    print("  OK  " + line)
