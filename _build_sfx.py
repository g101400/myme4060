# -*- coding: utf-8 -*-
"""构建 myme-setup.exe（7z 自解压精简安装包，只含完整版主程序）。

背景：v2.3.x 时代本机 WiX 打不出含大 exe 的 MSI，曾用 SFX 替代；现在 MSI 已能正常构建
（pack_full.py），但「安装与配置手册」的选包表仍保留 SFX 这一档（不想装 637MB 完整包、
也不需要绿色伴随目录的用户，双击即装主程序到 %LOCALAPPDATA%\\myme 并建快捷方式）。
门禁 _gate_292.py L3d 也断言 dist/myme-setup.exe 存在且 ≥10MB。

历史坑（v2.9.1）：构建链里没有脚本产 SFX，门禁却靠 dist/ 里 10-03 的陈旧 setup.exe
"假绿"——本脚本补上这一环，升版本后必须重跑，别让陈旧件充数。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "_pkg")
SEVEN_ZIP = r"C:/Program Files/7-Zip/7z.exe"
SFX_MODULE = r"C:/Program Files/7-Zip/7z.sfx"


def main():
    exe = os.path.join(HERE, "dist", "myme.exe")
    if not os.path.isfile(exe):
        print("[sfx] 找不到 dist/myme.exe，先跑 PyInstaller")
        return 1
    if not os.path.isfile(SFX_MODULE):
        print("[sfx] 找不到 7z.sfx：", SFX_MODULE)
        return 1

    os.makedirs(PKG, exist_ok=True)
    shutil.copy2(exe, os.path.join(PKG, "myme.exe"))

    # install.cmd（ASCII，cp936 兼容老 cmd）→ 调 powershell 跑 install.ps1
    install_cmd = (
        "@echo off\r\n"
        'powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n'
        "echo.\r\n"
        'explorer.exe "%LOCALAPPDATA%\\myme"\r\n'
        "echo.\r\n"
        "echo 安装完成，可关闭此窗口。\r\n"
        "pause\r\n"
    )
    with open(os.path.join(PKG, "install.cmd"), "w", encoding="cp936", newline="") as f:
        f.write(install_cmd)

    # install.ps1（UTF-8 BOM）→ 复制到 %LOCALAPPDATA%\myme 并建桌面/开始菜单快捷方式
    ps1 = (
        "$dst = Join-Path $env:LOCALAPPDATA 'myme'\r\n"
        "New-Item -ItemType Directory -Force -Path $dst | Out-Null\r\n"
        "Copy-Item -Force (Join-Path $PSScriptRoot 'myme.exe') (Join-Path $dst 'myme.exe')\r\n"
        "try {\r\n"
        "  $ws = New-Object -ComObject WScript.Shell\r\n"
        "  $desktop = [Environment]::GetFolderPath('Desktop')\r\n"
        "  $startmenu = Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'\r\n"
        "  ($s = $ws.CreateShortcut((Join-Path $desktop 'PPT\u5236\u4f5c\u53ca\u5206\u8eab\u6f14\u793a.lnk')); $s.TargetPath = (Join-Path $dst 'myme.exe'); $s.WorkingDirectory = $dst; $s.Description = '\u672c\u5730 PPT \u5236\u4f5c\u4e0e\u5206\u8eab\u6f14\u793a'; $s.Save()) | Out-Null\r\n"
        "  ($s2 = $ws.CreateShortcut((Join-Path $startmenu 'PPT\u5236\u4f5c\u53ca\u5206\u8eab\u6f14\u793a.lnk')); $s2.TargetPath = (Join-Path $dst 'myme.exe'); $s2.WorkingDirectory = $dst; $s2.Save()) | Out-Null\r\n"
        "  Write-Host '\u5df2\u521b\u5efa\u684c\u9762\u4e0e\u5f00\u59cb\u83c1\u5355\u5feb\u6377\u65b9\u5f0f\u3002'\r\n"
        "} catch {\r\n"
        "  Write-Host ('\u521b\u5efa\u5feb\u6377\u65b9\u5f0f\u5931\u8d25\uff08\u53ef\u624b\u52a8\u4ece ' + $dst + ' \u521b\u5efa\uff09\uff1a' + $_.Exception.Message)\r\n"
        "}\r\n"
    )
    with open(os.path.join(PKG, "install.ps1"), "w", encoding="utf-8-sig", newline="") as f:
        f.write(ps1)

    # SFX 配置（UTF-8 无 BOM）
    cfg = (
        ";!@Install@!UTF-8!\r\n"
        'Title="PPT制作及分身演示 安装程序"\r\n'
        'ExtractDialogText="正在释放文件…"\r\n'
        'RunProgram="install.cmd"\r\n'
        ";!@InstallEnd@!\r\n"
    )
    cfg_path = os.path.join(HERE, "config_sfx.txt")
    with open(cfg_path, "w", encoding="utf-8", newline="") as f:
        f.write(cfg)

    arc = os.path.join(HERE, "_pkg.7z")
    if os.path.exists(arc):
        os.remove(arc)
    subprocess.run([SEVEN_ZIP, "a", "-t7z", "-mx=7", arc,
                    os.path.join(PKG, "myme.exe"),
                    os.path.join(PKG, "install.cmd"),
                    os.path.join(PKG, "install.ps1")], check=True)

    out = os.path.join(HERE, "dist", "myme-setup.exe")
    with open(out, "wb") as f:
        f.write(open(SFX_MODULE, "rb").read())
        f.write(open(cfg_path, "rb").read())
        f.write(open(arc, "rb").read())
    print("[sfx] SFX built ->", out, os.path.getsize(out), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
