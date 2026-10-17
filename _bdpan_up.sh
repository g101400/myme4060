#!/usr/bin/env bash
# 上传 myme v2.9.4 交付物到百度网盘（仅本环境实出的产物；Win/安卓二进制须在构建机重出后另传）
REMOTE="myme_数字分身_v2.9.4_20261018"
cd /d/Users/WorkBuddy/Claw/myme || exit 1

up () {  # up <本地路径> <远端文件名>
  local SRC="$1" NAME="$2"
  if [ ! -f "$SRC" ]; then echo "[跳过] 不存在: $SRC"; return; fi
  local SZ; SZ=$(du -m "$SRC" | cut -f1)
  local T0; T0=$(date +%s)
  echo ">>> 开始上传 $NAME (${SZ}MB)  $(date +%H:%M:%S)"
  if bdpan upload "$SRC" "$REMOTE/$NAME" > /tmp/bdpan_$NAME.log 2>&1; then
    echo "<<< 完成 $NAME  用时 $(( $(date +%s) - T0 ))s"
  else
    echo "!!! 失败 $NAME  用时 $(( $(date +%s) - T0 ))s"; tail -5 /tmp/bdpan_$NAME.log
  fi
}

# —— 统信 deb ×5（all/amd64/arm64/mips64el/loongarch64）；龙芯 3A4000 取 mips64el ——
# （all.deb 已单独预传，此处不再传，避免同名 (1)）
up dist/myme-player_2.9.4_amd64.deb       myme-player_2.9.4_amd64.deb
up dist/myme-player_2.9.4_arm64.deb       myme-player_2.9.4_arm64.deb
up dist/myme-player_2.9.4_mips64el.deb    myme-player_2.9.4_mips64el.deb
up dist/myme-player_2.9.4_loongarch64.deb myme-player_2.9.4_loongarch64.deb

# —— 源码包 / iOS PWA / 便携播放版 / 校验表 ——
up dist/myme_source_2.9.4.zip             myme_source_2.9.4.zip
up dist_pkg/myme-ios-pwa.zip               myme-ios-pwa.zip
up "dist/水利一张图_数字分身演示_便携播放版_v2.9.4.zip" 水利一张图_数字分身演示_便携播放版_v2.9.4.zip
up dist/校验表_shasum_v2.9.4.txt           校验表_shasum_v2.9.4.txt

# —— 交接文档 zip ——
up "/d/Users/WorkBuddy/handover_myme_2026-10-16/myme_数字分身_交接_v2.9.4_20261018.zip" myme_数字分身_交接_v2.9.4_20261018.zip

echo "===== 上传结束 $(date +%H:%M:%S) ====="
bdpan ls "$REMOTE" 2>&1 | tail -25
