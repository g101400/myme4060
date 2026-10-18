; myme 安装包（NSIS）· v2.9.5
; 为什么用 NSIS 而不是 WiX：
;   本机 WiX 7 的 build 在本环境"退出码 0 但不产出文件"（cabor 阶段静默退出，
;   EULA 已接受仍如此），继续纠缠会拖住发版。NSIS 稳、体积小、perUser 免管理员，
;   且与历史 myme-setup.exe 的安装形态一致（装到 %LOCALAPPDATA%\myme + 桌面/开始菜单快捷方式）。
;   MSI 仍保留在构建脚本里（wix build myme.wxs），换台正常机器可随时补出。

Unicode true
SetCompressor /SOLID lzma

!define APPNAME     "PPT制作及分身演示"
!define APPNAME_EN  "myme"
!define VER         "2.9.5"
!define PUBLISHER   "myme"
!define EXENAME     "myme.exe"

Name "${APPNAME} ${VER}"
OutFile "dist\myme-${VER}-win-x64-setup.exe"
InstallDir "$LOCALAPPDATA\myme"
InstallDirRegKey HKCU "Software\myme" "InstallDir"
RequestExecutionLevel user
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "2.9.5.0"
VIAddVersionKey "ProductName"     "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} 安装程序"
VIAddVersionKey "FileVersion"     "${VER}.0"
VIAddVersionKey "ProductVersion"  "${VER}.0"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "LegalCopyright"  "本地部署 · 数据不出本机"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "主程序" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"
  File "dist\${EXENAME}"
  ; 启动脚本：装完直接双击快捷方式即可（myme.exe 自己会起服务并开浏览器）
  WriteRegStr HKCU "Software\myme" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\myme" "Version" "${VER}"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0
  CreateShortCut "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk" "$INSTDIR\uninstall.exe"
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0

  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "DisplayName" "${APPNAME} ${VER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "DisplayVersion" "${VER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\${EXENAME}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  Delete "$DESKTOP\${APPNAME}.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\myme"
  DeleteRegKey HKCU "Software\myme"
  ; 用户数据（%LOCALAPPDATA%\myme 下的项目/知识库/讲解音频）默认保留：
  ; 卸载不删数据是刻意设计，避免"卸了再装，资料全没了"。
SectionEnd
