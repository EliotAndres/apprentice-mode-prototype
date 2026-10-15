#!/bin/sh
# swift build -c release, wrap in Apprentice.app (LSUIElement), sign, install to ~/Applications.
# A stable (non ad-hoc) signature keeps the TCC grants valid across rebuilds.
set -e
cd "$(dirname "$0")/.."
SIGN_ID="${SIGN_ID:-E95B45C86619F69CB144DB18A604815762348840}" # Apple Development: Eliot Andres (6J84AXK2HG)
swift build -c release
APP="${APP:-$HOME/Applications/Apprentice.app}"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/Apprentice "$APP/Contents/MacOS/"
cp skill/SKILL.md "$APP/Contents/Resources/"   # the installer copies it to ~/.claude/skills/apprentice/
cp skill/feed.html "$APP/Contents/Resources/"  # installed as <data root>/feed/index.html
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>blog.eliot.apprentice</string>
<key>CFBundleName</key><string>Apprentice</string>
<key>CFBundleExecutable</key><string>Apprentice</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1</string>
<key>LSMinimumSystemVersion</key><string>15.0</string>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
codesign -s "$SIGN_ID" --force "$APP"
echo "$APP"
