#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PKG="$ROOT/macos/DokochanRuntime"
APP="$PKG/.build/DokochanRuntime.app"

(cd "$PKG" && swift build)

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$PKG/.build/debug/DokochanRuntime" "$APP/Contents/MacOS/DokochanRuntime"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>DokochanRuntime</string>
  <key>CFBundleIdentifier</key>
  <string>jp.kazuph.MotionPNGTuber.DokochanRuntime</string>
  <key>CFBundleName</key>
  <string>DokochanRuntime</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSScreenCaptureUsageDescription</key>
  <string>Dokochan lip sync reacts to macOS system audio captured with ScreenCaptureKit.</string>
  <key>NSAudioCaptureUsageDescription</key>
  <string>Dokochan lip sync reacts to macOS system audio captured with ScreenCaptureKit.</string>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsArbitraryLoads</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST

echo "$APP"
