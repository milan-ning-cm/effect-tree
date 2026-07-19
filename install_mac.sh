#!/bin/bash
# Effect Tree 安裝器(macOS):
#   1) ~/Applications/Effect Tree.app —— 雙擊/Spotlight 可開,拖進 Dock 即可釘選
#   2) LaunchAgent —— 登入時靜默自動啟動 server
# 執行: bash install_mac.sh   (移除: bash install_mac.sh uninstall)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"
APP="$HOME/Applications/Effect Tree.app"
LA="$HOME/Library/LaunchAgents/local.effecttree.server.plist"

if [ "$1" = "uninstall" ]; then
  launchctl unload "$LA" 2>/dev/null || true
  rm -rf "$APP"; rm -f "$LA"
  echo "已移除 Effect Tree.app 與登入自動啟動"; exit 0
fi

[ -n "$PY" ] || { echo "找不到 python3,請先安裝(xcode-select --install 或 brew install python)"; exit 1; }

mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Effect Tree</string>
  <key>CFBundleIdentifier</key><string>local.effecttree.launcher</string>
  <key>CFBundleExecutable</key><string>EffectTree</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/EffectTree" <<LAUNCH
#!/bin/bash
exec "$PY" "$HERE/tree.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/EffectTree"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$LA" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.effecttree.server</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string>
    <string>$HERE/tree.py</string>
    <string>--no-browser</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
launchctl unload "$LA" 2>/dev/null || true
launchctl load "$LA"

echo "已安裝:"
echo "  ~/Applications/Effect Tree.app  (開 Finder 拖進 Dock 即可釘選;Spotlight 搜 Effect Tree 也行)"
echo "  登入自動啟動 server(現在已在背景跑)"
echo "立即打開編輯器: open '$APP'  或瀏覽器開 http://127.0.0.1:8778"
