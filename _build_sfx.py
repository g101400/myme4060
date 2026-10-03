# -*- coding: utf-8 -*-
import os, subprocess

# 【2026-10-03 修复】原写死 D:/Users/Claw/myme（旧工作区），本机实际是 D:/Users/WorkBuddy/Claw/myme，
# 打包时 7z 里根本没有 myme.exe、SFX 产物残缺。改为相对脚本目录，换机/迁目录都不会失效。
BASE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(BASE, '_pkg')
SEVEN_ZIP = r'C:/Program Files/7-Zip/7z.exe'
SFX = r'C:/Program Files/7-Zip/7z.sfx'

os.makedirs(PKG, exist_ok=True)

# 1) 复制完整版 exe
import shutil
shutil.copy2(os.path.join(BASE, 'dist', 'myme.exe'),
             os.path.join(PKG, 'myme.exe'))

# 2) install.cmd (ASCII) -> 调用 powershell 执行 install.ps1
install_cmd = (
    '@echo off\r\n'
    'powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"\r\n'
    'echo.\r\n'
    'explorer.exe "%LOCALAPPDATA%\\myme"\r\n'
    'echo.\r\n'
    'echo 安装完成，可关闭此窗口。\r\n'
    'pause\r\n'
)
with open(os.path.join(PKG, 'install.cmd'), 'w', encoding='cp936', newline='') as f:
    f.write(install_cmd)

# 3) install.ps1 (UTF-8 BOM) -> 复制到 LocalAppData\myme 并建快捷方式
ps1 = (
    '$dst = Join-Path $env:LOCALAPPDATA \'myme\'\r\n'
    'New-Item -ItemType Directory -Force -Path $dst | Out-Null\r\n'
    'Copy-Item -Force (Join-Path $PSScriptRoot \'myme.exe\') (Join-Path $dst \'myme.exe\')\r\n'
    'try {\r\n'
    '  $ws = New-Object -ComObject WScript.Shell\r\n'
    '  $desktop = [Environment]::GetFolderPath(\'Desktop\')\r\n'
    '  $startmenu = Join-Path ([Environment]::GetFolderPath(\'StartMenu\')) \'Programs\'\r\n'
    '  ($s = $ws.CreateShortcut((Join-Path $desktop \'PPT\u5236\u4f5c\u53ca\u5206\u8eab\u6f14\u793a.lnk\')); $s.TargetPath = (Join-Path $dst \'myme.exe\'); $s.WorkingDirectory = $dst; $s.Description = \'\u672c\u5730 PPT \u5236\u4f5c\u4e0e\u5206\u8eab\u6f14\u793a\'; $s.Save()) | Out-Null\r\n'
    '  ($s2 = $ws.CreateShortcut((Join-Path $startmenu \'PPT\u5236\u4f5c\u53ca\u5206\u8eab\u6f14\u793a.lnk\')); $s2.TargetPath = (Join-Path $dst \'myme.exe\'); $s2.WorkingDirectory = $dst; $s2.Save()) | Out-Null\r\n'
    '  Write-Host \'\u5df2\u521b\u5efa\u684c\u9762\u4e0e\u5f00\u59cb\u83dc\u5355\u5feb\u6377\u65b9\u5f0f\u3002\'\r\n'
    '} catch {\r\n'
    '  Write-Host ("\u521b\u5efa\u5feb\u6377\u65b9\u5f0f\u5931\u8d25\uff08\u53ef\u624b\u52a8\u4ece " + $dst + " \u521b\u5efa\uff09\uff1a" + $_.Exception.Message)\r\n'
    '}\r\n'
)
with open(os.path.join(PKG, 'install.ps1'), 'w', encoding='utf-8-sig', newline='') as f:
    f.write(ps1)

# 4) config.txt (UTF-8 without BOM) for SFX
cfg = (
    ';!@Install@!UTF-8!\r\n'
    'Title="PPT制作及分身演示 安装程序"\r\n'
    'ExtractDialogText="正在释放文件…"\r\n'
    'RunProgram="install.cmd"\r\n'
    ';!@InstallEnd@!\r\n'
)
with open(os.path.join(BASE, 'config_sfx.txt'), 'w', encoding='utf-8', newline='') as f:
    f.write(cfg)

# 5) 压缩 _pkg
arc = os.path.join(BASE, '_pkg.7z')
if os.path.exists(arc):
    os.remove(arc)
subprocess.run([SEVEN_ZIP, 'a', '-t7z', '-mx=7', arc,
               os.path.join(PKG, 'myme.exe'),
               os.path.join(PKG, 'install.cmd'),
               os.path.join(PKG, 'install.ps1')], check=True)

# 6) 拼接 sfx + config + archive
sfx = open(SFX, 'rb').read()
cfgb = open(os.path.join(BASE, 'config_sfx.txt'), 'rb').read()
arb = open(arc, 'rb').read()
out = os.path.join(BASE, 'dist', 'myme-setup.exe')
with open(out, 'wb') as f:
    f.write(sfx + cfgb + arb)
print('SFX built ->', out, os.path.getsize(out), 'bytes')
