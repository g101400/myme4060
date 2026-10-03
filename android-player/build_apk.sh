#!/usr/bin/env bash
# myme 便携播放端 APK：手动用 aapt2 + javac + d8 + zipalign + apksigner 构建
# 免 Gradle（本机没配 Gradle 代理），与 水利/古建/感知 三端同一套离线工具链。
#   用法： bash build_apk.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
# 【2026-10-03 修复】原默认 D:/Android（本机不存在）→ APK 构建直接 fatal。
# 改为多候选探测：环境变量 > 本机常见 SDK 位置（WorkBuddy 内置 / 用户级 / 常见盘符）。
SDK="${ANDROID_SDK_ROOT:-}"
if [ -z "$SDK" ]; then
  for _c in "$HOME/.workbuddy/android-sdk" "$HOME/AppData/Local/Android/Sdk" "/c/Android" "/d/Android" "/d/Android/sdk"; do
    if [ -d "$_c/build-tools" ]; then SDK="$(cd "$_c" && pwd)"; break; fi
  done
fi
SDK="${SDK:-D:/Android}"
BT="$SDK/build-tools/34.0.0"
PLATFORM="$SDK/platforms/android-34/android.jar"
AAPT2="$BT/aapt2.exe"
D8="$BT/d8.bat"
ZIPALIGN="$BT/zipalign.exe"
APKSIGNER="$BT/apksigner.bat"
KS="${MYME_KS:-/d/Users/Claw/android-build/shuili-v329/keystore.jks}"
KS_ALIAS="${APK_KS_ALIAS:-xitian}"
KS_PASS="${APK_KS_PASS:-xitian123}"
KEY_PASS="${APK_KEY_PASS:-$KS_PASS}"

cd "$HERE"
rm -f res_compiled.zip app-unsigned.apk app-aligned.apk app-release.apk 2>/dev/null || true

echo "[0] 门禁：assets/player.html 与 assets/manifest.json 必须在位"
[ -f assets/player.html ]   || { echo "[0][FATAL] 缺 assets/player.html（先跑 pack_portable.py 灌入播放包）"; exit 1; }
[ -f assets/manifest.json ] || { echo "[0][FATAL] 缺 assets/manifest.json"; exit 1; }

echo "[1] 编译资源 res -> res_compiled.zip"
"$AAPT2" compile --dir res -o res_compiled.zip

echo "[2] 链接资源 + 打包 assets -> app-unsigned.apk"
"$AAPT2" link -o app-unsigned.apk -I "$PLATFORM" \
  --manifest AndroidManifest.xml -A assets res_compiled.zip

echo "[3] 编译 Java"
rm -rf obj; mkdir -p obj
javac -encoding UTF-8 -cp "$PLATFORM" -d obj src/com/myme/player/MainActivity.java

echo "[4] 转 DEX"
rm -rf dex; mkdir -p dex
CLASSES=$(find obj -name "*.class")
"$D8" --lib "$PLATFORM" --output dex $CLASSES

echo "[5] 注入 classes.dex（STORED，否则 Android 不加载）"
PY="${PY_EXE:-python}"
"$PY" - app-unsigned.apk dex/classes.dex <<'PYEOF'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, 'a', compression=zipfile.ZIP_STORED) as z:
    if 'classes.dex' not in z.namelist():
        z.write(dex, 'classes.dex')
print('classes.dex injected; entries:', len(zipfile.ZipFile(apk).namelist()))
PYEOF

echo "[6] zipalign"
"$ZIPALIGN" -f -p 4 app-unsigned.apk app-aligned.apk

echo "[7] 签名 -> app-release.apk"
"$APKSIGNER" sign --ks "$KS" --ks-key-alias "$KS_ALIAS" \
  --ks-pass "pass:$KS_PASS" --key-pass "pass:$KEY_PASS" \
  --out app-signed.apk app-aligned.apk
mv -f app-signed.apk app-release.apk

echo "[8] 发布门禁"
BADGING=$("$AAPT2" dump badging app-release.apk 2>/dev/null)
echo "$BADGING" | grep -q "^launchable-activity:" \
  || { echo "[8][FATAL] 无 launchable-activity（安装后没桌面图标）"; exit 1; }
"$APKSIGNER" verify app-release.apk >/dev/null 2>&1 \
  || { echo "[8][FATAL] apksigner verify 失败"; exit 1; }
echo "[8] 门禁全过：launchable-activity OK / 签名 OK"

echo "[完成] app-release.apk"
ls -la app-release.apk
