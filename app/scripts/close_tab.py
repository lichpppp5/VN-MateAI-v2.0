# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
import pyautogui
import sys

def close_active_tab():
    """Gửi phím nóng Ctrl+W để đóng tab hiện tại mà không tắt cả trình duyệt."""
    pyautogui.hotkey('ctrl', 'w')
    print("Đã gửi lệnh đóng tab hiện tại (Ctrl+W)")

if __name__ == '__main__':
    close_active_tab()
