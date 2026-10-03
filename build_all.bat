@echo off
setlocal
cd /d "%~dp0"

REM ============================================================
REM  Build both deployable versions in one go:
REM    - Full version    dist\myme.exe + dist\myme-setup.msi
REM    - Portable play-only version  dist\myme-play.exe
REM ============================================================
echo ===== Building full version =====
call build_full.bat
if errorlevel 1 ( echo [!] full version build failed. & exit /b 1 )

echo.
echo ===== Building portable play-only version =====
call build_portable.bat
if errorlevel 1 ( echo [!] portable version build failed. & exit /b 1 )

echo.
echo ===== All done =====
echo   dist\myme.exe          full version (web service)
echo   dist\myme-setup.msi    full version installer (if wix available)
echo   dist\myme-play.exe     portable play-only version
endlocal
