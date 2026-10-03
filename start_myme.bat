@echo off
chcp 65001 >nul 2>&1
setlocal
cd /d "%~dp0"

REM UTF-8 输出，避免中文乱码
set PYTHONIOENCODING=utf-8
set MYME_PORT=7860

echo ============================================================
echo   炎冰数字分身 · 一键启动
echo   顺序：① 常驻 TTS(炎冰声纹) ② 常驻数字人(LivePortrait) ③ Web
echo   退出（Ctrl+C 或关闭窗口）会自动收尾所有后台进程
echo ============================================================
echo.

REM 优先用 PATH 中的 python，否则退回 py 启动器
where python >nul 2>nul
if errorlevel 1 ( set "PY=py" ) else ( set "PY=python" )

"%PY%" start_myme.py
set RC=%errorlevel%

echo.
if not "%RC%"=="0" (
  echo [!] start_myme.py 异常退出（返回码 %RC%），详见日志 DIR_TEST/*.log
) else (
  echo 已正常退出。
)
pause
endlocal
