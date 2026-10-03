# -*- mode: python ; coding: utf-8 -*-
# 便携播放版打包规格（myme-play.exe）
# 与完整版(myne.spec)同源，仅两点差异：
#   1) 注入空文件 playback.mode 到打包资源根目录 -> 运行时 config 检测到即进入"播放版"，
#      自动关闭 TTS / LivePortrait 常驻服务（播放只串流已渲染音频/视频，无需模型）。
#   2) 可执行文件名改为 myme-play，便于与完整版区分。
# 注意：playback.mode 由 build_portable.bat 在打包前临时生成、打包后删除，避免污染源码树。

# 与完整版同源：同样附上子进程服务脚本与全部声纹（见 myme.spec 顶部说明），
# 只额外注入 playback.mode 进入「只播放」态（自动关闭 TTS / LivePortrait 常驻服务）。
import glob as _glob

_aug_files = [(p, '.') for p in sorted(_glob.glob('*.py'))]
_voice_files = [(p, 'voice') for p in sorted(_glob.glob('voice/*.wav'))]

a = Analysis(
    ['myme_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('ui/index.html', 'ui'), ('ui/player.html', 'ui'),
           ('icon-192.png', '.'), ('icon-512.png', '.'),
           ('playback.mode', '.')] + _aug_files + _voice_files,
    hiddenimports=['pypdf', 'doc_ingest', 'prompts.system_prompt'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='myme-play',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='myme.ico',
    version='version_info.txt',
)
