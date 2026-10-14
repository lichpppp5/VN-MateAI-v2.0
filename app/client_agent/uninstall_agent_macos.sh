#!/bin/bash
# VN-MateAI Agent — gỡ trên macOS (bản Python). --remove-venv: xoá luôn .venv.
# Không xoá config.json / device.json / logs — xoá thư mục Agent bằng tay nếu muốn.
set -uo pipefail
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/vn.mateai.agent.plist"
if [ -f "$PLIST" ]; then
  launchctl unload -w "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "Đã gỡ LaunchAgent vn.mateai.agent."
else
  echo "Không có LaunchAgent vn.mateai.agent."
fi
pkill -f "$AGENT_DIR/agent.py" >/dev/null 2>&1 && echo "Đã dừng Agent." || true
if [ "${1:-}" = "--remove-venv" ]; then rm -rf "$AGENT_DIR/.venv" && echo "Đã xoá .venv."; fi
echo "Gỡ xong."
