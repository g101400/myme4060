# -*- coding: utf-8 -*-
"""统信 deb 独立复检（不复用 pack_portable 的代码路径，防止自证）。"""
import io, lzma, tarfile, sys, hashlib
path = sys.argv[1] if len(sys.argv) > 1 else "dist/myme-player_2.8.0_all.deb"
raw = open(path, "rb").read()
ok = 0; bad = 0
def chk(name, cond, extra=""):
    global ok, bad
    if cond: ok += 1; print("  [OK] %s %s" % (name, extra))
    else:    bad += 1; print("  [XX] %s %s" % (name, extra))

print("deb: %s  %d bytes" % (path, len(raw)))
print("md5 : %s" % hashlib.md5(raw).hexdigest())
print("sha256: %s" % hashlib.sha256(raw).hexdigest())
chk("ar 头", raw.startswith(b"!<arch>\n"))
pos = 8; seen = {}
while pos + 60 <= len(raw):
    nm = raw[pos:pos+16].decode("ascii","ignore").strip()
    size = int(raw[pos+48:pos+58].decode("ascii","ignore").strip() or 0)
    seen[nm] = raw[pos+60:pos+60+size]
    pos += 60 + size + (size % 2)
chk("ar 三成员", set(seen) >= {"debian-binary","control.tar.gz","data.tar.xz"}, str(sorted(seen)))
chk("debian-binary=2.0", seen.get("debian-binary") == b"2.0\n")

# control.tar.gz
with tarfile.open(fileobj=io.BytesIO(seen["control.tar.gz"])) as ct:
    cs = {m.name[2:] if m.name.startswith("./") else m.name.lstrip("/"): m for m in ct.getmembers()}
    chk("control 成员齐备", set(cs) >= {"control","postinst","prerm","postrm"}, str(sorted(cs)))
    for k in ("postinst","prerm","postrm"):
        chk("control/%s 0o755" % k, bool(cs[k].mode & 0o111), oct(cs[k].mode & 0o777))
    pi = ct.extractfile(cs["postinst"]).read().decode("utf-8","ignore")
    chk("postinst 无 CRLF", "\r\n" not in pi)
    chk("postinst 无 set -e（行级）",
        not [l for l in pi.splitlines() if l.strip() in ("set -e","set -eu","set -ex","set -euo pipefail")])
    chk("postinst chmod 有容错", "|| true" in pi)
    chk("postinst 有完整性自检", "安装不完整" in pi)
    ctl = ct.extractfile(cs["control"]).read().decode("utf-8","ignore")
    chk("Architecture: all", "Architecture: all" in ctl)
    chk("Depends 仅 python3", "Depends: python3\n" in ctl and "xdg-utils" not in ctl.split("Recommends")[0])

# data.tar.xz
with tarfile.open(fileobj=io.BytesIO(lzma.decompress(seen["data.tar.xz"]))) as tf:
    ms = tf.getmembers()
    nm2m = {}
    for m in ms:
        nm2m[m.name[2:] if m.name.startswith("./") else m.name.lstrip("/")] = m
    chk("无 PAX 扩展头", not [m for m in ms if m.type in ("x","g")])
    chk("无顶层散落文件", not [m for m in ms if (not m.isdir()) and m.name.replace("./","",1).count("/")==0])
    chk("目录项含 opt 与 usr", nm2m.get("opt") is not None and nm2m.get("usr") is not None)
    lp = nm2m.get("opt/myme-player/myme-player")
    chk("启动器存在", lp is not None)
    chk("启动器 0o755（不再依赖 postinst）", lp is not None and bool(lp.mode & 0o111),
        oct(lp.mode & 0o777) if lp else "-")
    d = tf.extractfile(lp).read()
    chk("启动器无 CRLF", b"\r\n" not in d, "size=%d" % len(d))
    chk("启动器首行 #!/bin/sh", d.split(b"\n")[0] == b"#!/bin/sh", repr(d.split(b"\n")[0]))
    for rel in ("opt/myme-player/serve.py", "usr/share/applications/myme-player.desktop",
                "usr/bin/myme-player"):
        m = nm2m.get(rel)
        chk("%s 存在" % rel, m is not None)
        if m is not None:
            chk("%s 无 CRLF" % rel, b"\r\n" not in tf.extractfile(m).read())
    ub = nm2m.get("usr/bin/myme-player")
    chk("usr/bin/myme-player 0o755", ub is not None and bool(ub.mode & 0o111))
    chk("opt/myme-player/icon.png 存在", nm2m.get("opt/myme-player/icon.png") is not None)
    de = tf.extractfile(nm2m["usr/share/applications/myme-player.desktop"]).read().decode("utf-8","ignore")
    chk(".desktop Exec 绝对路径", "Exec=/usr/bin/myme-player" in de)
    chk(".desktop Icon 绝对路径", "Icon=/opt/myme-player/icon.png" in de)
    for rel in ("opt/myme-player/player.html","opt/myme-player/manifest.json","opt/myme-player/manifest.js"):
        chk("载荷 %s" % rel, nm2m.get(rel) is not None)
    chk("载荷 player.html 含导入三通路", b"pickInput" in tf.extractfile(nm2m["opt/myme-player/player.html"]).read())
    chk("载荷 manifest.json 含 params", b'"params"' in tf.extractfile(nm2m["opt/myme-player/manifest.json"]).read())
    chk("无中文文件名", not [m.name for m in ms if any(ord(c) > 127 for c in m.name)])
    chk("成员数 > 200", len(ms) > 200, str(len(ms)))

print("\n通过 %d 项，失败 %d 项" % (ok, bad))
print("结论：%s" % ("✅ ALL OK" if bad == 0 else "❌ 有失败项"))
sys.exit(1 if bad else 0)
