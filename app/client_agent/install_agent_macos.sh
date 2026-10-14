#!/bin/bash
# VN-MateAI Agent — cài trên macOS (bản Python, không cần build).
#
#   1. Tìm python3 >= 3.10 (Homebrew / python.org).
#   2. Tạo .venv trong thư mục Agent, cài thư viện (requirements.txt).
#   3. Đăng ký LaunchAgent "vn.mateai.agent": chạy nền mỗi khi đăng nhập, tự chạy lại nếu dừng.
#   4. Khởi động ngay. Log: logs/agent.log
#
# Dùng:  cd <thư mục Agent> && bash install_agent_macos.sh
# Gỡ:    bash uninstall_agent_macos.sh
# Bản build một file (không cần Python): xem HUONG_DAN_CAI_DAT.txt trong gói macOS.
set -euo pipefail

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="vn.mateai.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$(command -v "$cand")"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "Không tìm thấy Python 3.10+. Cài từ https://www.python.org/downloads/macos/ (hoặc: brew install python) rồi chạy lại." >&2
  exit 1
fi
echo "==> Python: $PY"

if [ ! -f "$AGENT_DIR/config.json" ]; then
  echo "Thiếu config.json — tải lại gói Agent từ Portal." >&2; exit 1
fi
if [ ! -f "$AGENT_DIR/device.json" ] && ! grep -q '"enroll_code"' "$AGENT_DIR/config.json"; then
  echo "config.json không có mã đăng ký (enroll_code) và máy chưa đăng ký — tải gói Agent MỚI từ Portal." >&2; exit 1
fi

echo "==> Tạo môi trường ảo .venv và cài thư viện"
[ -x "$AGENT_DIR/.venv/bin/python" ] || "$PY" -m venv "$AGENT_DIR/.venv"
"$AGENT_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q --upgrade pip
"$AGENT_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$AGENT_DIR/requirements.txt"

echo "==> Đăng ký LaunchAgent $LABEL"
mkdir -p "$HOME/Library/LaunchAgents" "$AGENT_DIR/logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$AGENT_DIR/.venv/bin/python</string><string>$AGENT_DIR/agent.py</string>
  </array>
  <key>WorkingDirectory</key><string>$AGENT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardErrorPath</key><string>$AGENT_DIR/logs/launchd.err.log</string>
</dict></plist>
PLIST
launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load -w "$PLIST"

sleep 5
tail -n 5 "$AGENT_DIR/logs/agent.log" 2>/dev/null || true
echo
echo "Đã cài xong. Agent chạy nền và tự chạy khi đăng nhập."
echo "Nếu cần chụp màn hình máy trạm: System Settings -> Privacy & Security -> Screen Recording -> cho phép Python."
echo "Gỡ: bash uninstall_agent_macos.sh"
