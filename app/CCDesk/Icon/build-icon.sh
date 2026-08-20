#!/bin/sh
# 从 SVG 源码生成 AppIcon.icns。
#   ./build-icon.sh [输出路径]     默认同目录 AppIcon.icns
#
# 两套源码不是冗余，是必需的：
#   icon-full.svg   128px 以上。三条会话行 + 辉光，细节完整。
#   icon-small.svg  16/32px。细行在这个尺寸必糊成一片，所以简化成
#                   「一条行 + 一个光标」，并去掉辉光（小尺寸只会变脏点）。
# 一套源码缩到 16px 的结果实测是一团糊，别想省这一步。
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$DIR/AppIcon.icns}
command -v rsvg-convert >/dev/null || { echo "缺 rsvg-convert：brew install librsvg" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
SET="$WORK/CCDesk.iconset"
mkdir -p "$SET"

gen() { rsvg-convert -w "$2" -h "$2" "$DIR/$1" -o "$SET/$3"; }
gen icon-small.svg 16   icon_16x16.png
gen icon-small.svg 32   icon_16x16@2x.png
gen icon-small.svg 32   icon_32x32.png
gen icon-small.svg 64   icon_32x32@2x.png
gen icon-full.svg  128  icon_128x128.png
gen icon-full.svg  256  icon_128x128@2x.png
gen icon-full.svg  256  icon_256x256.png
gen icon-full.svg  512  icon_256x256@2x.png
gen icon-full.svg  512  icon_512x512.png
gen icon-full.svg  1024 icon_512x512@2x.png

iconutil -c icns "$SET" -o "$OUT"
echo "已生成 $OUT"
