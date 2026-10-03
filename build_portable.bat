@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Portable play-only version: PyInstaller (onefile) -> dist\myme-play.exe
REM  Triggers play-only mode by dropping a temporary playback.mode file
REM  (PLAYBACK_MODE turns OFF the heavy TTS / avatar model services and
REM  hides the authoring menus). Used to play files exported by the full
REM  version.
REM ============================================================
echo [1/1] PyInstaller building portable play-only version (dist\myme-play.exe)...
echo. > playback.mode
python -m PyInstaller myme_portable.spec --noconfirm --clean
set RC=%errorlevel%
if exist playback.mode del /f playback.mode
if %RC% neq 0 ( echo [!] PyInstaller failed & exit /b 1 )

echo.
echo [done] Portable play-only version: dist\myme-play.exe
echo   Run it -> select tab 8 "Import playback file" -> choose the .zip exported by the
echo   full version -> go to tab 5 to play.
echo   Play-only version does NOT need TTS / avatar models; it only streams pre-rendered
echo   audio and video.
endlocal
