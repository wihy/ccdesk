#!/bin/sh
# 把 release 二进制装进标准 .app bundle。
#   ./build-app.sh [输出路径]        默认 ~/Applications/CCDesk.app
#
# 打包的关键收益不是图标，是 CFBundleIdentifier：裸 SPM 可执行文件的
# Bundle.main.bundleIdentifier 是 nil，AppDelegate 里两处 bundle 守卫因此挡住通知；
# 有了 bundle 它们自然放行，通知随之可用（守卫逻辑本身不用改）。
set -eu

APP_DIR=$(cd "$(dirname "$0")" && pwd)
OUT=${1:-$HOME/Applications/CCDesk.app}
VERSION=0.1.0

(cd "$APP_DIR" && swift build -c release)
BIN="$APP_DIR/.build/release/CCDesk"
[ -x "$BIN" ] || { echo "构建产物不存在：$BIN" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS"
cp "$BIN" "$OUT/Contents/MacOS/CCDesk"

cat > "$OUT/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleIdentifier</key>
	<string>com.ccdesk.app</string>
	<key>CFBundleExecutable</key>
	<string>CCDesk</string>
	<key>CFBundleName</key>
	<string>CCDesk</string>
	<key>CFBundleDisplayName</key>
	<string>CCDesk</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleShortVersionString</key>
	<string>${VERSION}</string>
	<key>CFBundleVersion</key>
	<string>${VERSION}</string>
	<key>NSPrincipalClass</key>
	<string>NSApplication</string>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>
	<!-- 只在菜单栏，不进 Dock、不抢 App 切换器（等价现在 main.swift 的 .accessory） -->
	<key>LSUIElement</key>
	<true/>
</dict>
</plist>
PLIST

plutil -lint "$OUT/Contents/Info.plist"

# ad-hoc 重签，只为把**签名标识**改成 bundle id（不是开发者签名/公证）。
# swift build 产物本来就是 adhoc linker-signed，但标识是 "CCDesk"，与
# CFBundleIdentifier 不符，usernotificationsd 会以缺 private entitlement 为由拒掉
# requestAuthorization（实测日志：requestAuthorization not allowed: com.ccdesk.app）。
codesign --force --sign - --identifier com.ccdesk.app "$OUT"

echo "✅ 已打包：$OUT"
