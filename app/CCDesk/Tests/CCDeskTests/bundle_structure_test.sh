#!/bin/sh
# build-app.sh 的打包产物结构测试（真跑脚本，装到临时目录，不碰 ~/Applications）。
#   sh Tests/CCDeskTests/bundle_structure_test.sh
# 不放进 swift test：在 swift test 里再跑一次 swift build -c release 会撞同一个
# .build 锁，风险大于收益。
set -eu

APP_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT="$APP_DIR/build-app.sh"
OUT="$(mktemp -d)/CCDesk.app"
PLIST="$OUT/Contents/Info.plist"
EXEC="$OUT/Contents/MacOS/CCDesk"
fails=0

ok()   { echo "  ok   — $1"; }
fail() { echo "  FAIL — $1"; fails=$((fails + 1)); }

check() {
    if eval "$2" >/dev/null 2>&1; then ok "$1"; else fail "$1"; fi
}

expect_key() {  # expect_key <键名> <期望值>
    actual=$(plutil -extract "$1" raw -o - "$PLIST" 2>/dev/null || echo "<缺失>")
    if [ "$actual" = "$2" ]; then ok "Info.plist $1 = $2"
    else fail "Info.plist $1 期望 $2，实际 $actual"; fi
}

echo "build-app.sh → $OUT"
if [ ! -f "$SCRIPT" ]; then
    echo "  FAIL — build-app.sh 不存在：$SCRIPT"
    echo "1 failed"
    exit 1
fi
sh "$SCRIPT" "$OUT" >/dev/null

echo "断言："
check "Contents/MacOS/CCDesk 存在"       "[ -f '$EXEC' ]"
check "Contents/MacOS/CCDesk 可执行"     "[ -x '$EXEC' ]"
check "Contents/Info.plist 存在"          "[ -f '$PLIST' ]"
check "Info.plist 语法合法（plutil -lint）" "plutil -lint '$PLIST'"
expect_key CFBundleIdentifier com.ccdesk.app
expect_key CFBundleExecutable CCDesk
expect_key LSUIElement true

# 签名标识必须等于 bundle id，否则 usernotificationsd 直接拒：
#   Entitlement 'com.apple.private.usernotifications.bundle-identifiers' required
#   requestAuthorization not allowed: com.ccdesk.app
# swift build 出来的二进制是 adhoc linker-signed、标识为 "CCDesk"，对不上。
# 图标：Resources/AppIcon.icns 与 Info.plist 的 CFBundleIconFile 必须成对存在。
# 少一半就是「装了图标但系统不认」——Finder 里仍显示白纸默认图标，且不报任何错。
check "Contents/Resources/AppIcon.icns 存在" "[ -f '$OUT/Contents/Resources/AppIcon.icns' ]"
expect_key CFBundleIconFile AppIcon

ident=$(codesign -dv "$OUT" 2>&1 | sed -n 's/^Identifier=//p')
if [ "$ident" = "com.ccdesk.app" ]; then ok "签名标识 = com.ccdesk.app"
else fail "签名标识期望 com.ccdesk.app，实际 ${ident:-<未签名>}"; fi

rm -rf "$(dirname "$OUT")"
if [ "$fails" -eq 0 ]; then echo "全部通过"; else echo "$fails failed"; exit 1; fi
