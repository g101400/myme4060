# -*- mode: python ; coding: utf-8 -*-
# 完整版打包规格（myme.exe）
#
# ⚠ 关键：TTS / 数字人服务由【外部 ComfyUI 嵌入式 python】拉起（见 config.EMBEDDED_PY），
#   它们拿不到 PyInstaller 内部 PYZ 里的模块，只能读真实 .py 文件。
#   因此必须把子进程要跑的服务脚本 + 其本地依赖 + 声纹资源一并作为 datas 打进包根，
#   否则启动闪退："can't open file '..._MEIxxxxx\tts_service.py'"，表现为
#   「分身声音合成失败（TTS 服务/子进程不可用）」。
#   这里用 glob 收集根级 .py 与 voice/*.wav，杜绝逐个手工登记时漏项。
import glob as _glob

_aug_files = [(p, '.') for p in sorted(_glob.glob('*.py'))]
_voice_files = [(p, 'voice') for p in sorted(_glob.glob('voice/*.wav'))]
# 内置分身演示片段（2026-10-03 起）：炎冰/金子 Sonic 出片，供 APP「验证环境」演示模态
# 直接播放（/api/demo_avatar_clip），无需用户先跑一次推理即可确认本机环境可用。
# 目录整体打入 bundle 根，运行时从 HERE/demo_avatar 读取（app.py: /api/demo_avatar_clip）。
_demo_clips = [(p, 'demo_avatar') for p in sorted(_glob.glob('demo_avatar/*.mp4'))]

a = Analysis(
    ['myme_launcher.py'],
    pathex=[],
    binaries=[],
    # ui/player.html：便携播放包用的离线播放器，导出时要能读到并塞进 zip
    datas=[('ui/index.html', 'ui'), ('ui/player.html', 'ui'),
           ('icon-192.png', '.'), ('icon-512.png', '.')]
           + _aug_files + _voice_files + _demo_clips,
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
    name='myme',
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
