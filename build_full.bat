@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Full version packaging: PyInstaller (web) + WiX (MSI installer)
REM  Full version has all menus (prepare / QA / avatar video / KB /
REM  demo / summary / resource mgmt) and exposes a "Export playback
REM  file" button in tab 5 for the portable play-only version.
REM ============================================================
echo [1/2] PyInstaller building full version (dist\myme.exe)...
python -m PyInstaller myme.spec --noconfirm --clean
if errorlevel 1 ( echo [!] PyInstaller failed & exit /b 1 )

echo [2/2] WiX MSI (dist\myme-setup.msi)...
where wix >nul 2>nul
if errorlevel 1 (
  echo [!] wix CLI not found - skipping MSI; dist\myme.exe is the standalone web service.
  echo     To build MSI: install WiX Toolset and ensure 'wix' is on PATH.
) else (
  wix build myme.wxs -o dist\myme-setup.msi
  if errorlevel 1 ( echo [!] WiX build failed - check myme.wxs and dist\myme.exe. )
)

echo.
echo [done] Full version artifacts:
echo   dist\myme.exe        (portable web service, double-click to run)
echo   dist\myme-setup.msi  (installer, if wix available)
echo.
echo Note: full version needs TTS + avatar services (start_myme.bat) to speak / generate video.
endlocal
