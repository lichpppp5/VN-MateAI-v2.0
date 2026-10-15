# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Đặt lại mật khẩu KHẨN CẤP — dùng khi chính quản trị viên quên mật khẩu (không còn ai trong portal cấp lại được).

Chạy trên MÁY CHỦ (quyền vào máy chủ = bằng chứng thẩm quyền):
    python scripts/reset_password.py admin                 # sinh mật khẩu tạm ngẫu nhiên, in ra một lần
    python scripts/reset_password.py admin --reset-mfa     # thêm: gỡ MFA (mất cả điện thoại xác thực)
    python scripts/reset_password.py --list                # liệt kê tài khoản admin
Docker:
    docker compose exec app python scripts/reset_password.py admin

Mật khẩu tạm buộc đổi ở lần đăng nhập tới; mở khoá đăng nhập sai nhiều lần; đóng yêu cầu quên mật khẩu đang mở; ghi kiểm toán
(PASSWORD_RESET_CLI). Không bao giờ ghi mật khẩu ra tệp / log — chỉ in ra màn hình này.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import socket
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def temp_password(n: int = 14) -> str:
    """Chữ + số, bỏ ký tự dễ nhầm (0/O, 1/l/I); luôn có cả chữ và số (đạt chính sách khi người dùng đặt lại)."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isalpha() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Đặt lại mật khẩu khẩn cấp (chạy trên máy chủ)")
    ap.add_argument("username", nargs="?", help="tài khoản cần đặt lại")
    ap.add_argument("--reset-mfa", action="store_true", help="gỡ luôn xác thực hai lớp của tài khoản")
    ap.add_argument("--list", action="store_true", help="liệt kê tài khoản quản trị")
    args = ap.parse_args(argv)

    from mateai.infrastructure.database.db_manager import db_manager

    if args.list:
        for u in db_manager.get_all_users():
            if str(u.get("role")).lower() == "admin":
                print(f"  {u['username']:<20} {u.get('full_name', '')}")
        return 0
    if not args.username:
        ap.error("cần tên tài khoản (hoặc --list)")

    user = db_manager.get_user_by_username_or_id(args.username)
    if not user:
        print(f"Không có tài khoản '{args.username}'. Xem danh sách admin: python scripts/reset_password.py --list")
        return 1
    pwd = temp_password()
    db_manager.change_user_password(user["id"], pwd, must_change=True)
    if args.reset_mfa:
        from mateai.application.security import mfa
        mfa.disable(user["username"])
    try:
        from mateai.infrastructure.cache import shared_state
        shared_state.store().events_clear("login:user:" + user["username"].lower())
    except Exception:  # noqa: BLE001 — chưa có Redis / kho dùng chung: khoá tự hết sau vài phút
        pass
    from mateai.application.security import password_reset
    password_reset.close_for_user(user["username"], "cli")
    password_reset._audit(f"cli:{getpass.getuser()}@{socket.gethostname()}", "PASSWORD_RESET_CLI", user["username"], True,
                          {"reset_mfa": bool(args.reset_mfa)})
    print(f"Đã đặt mật khẩu tạm cho '{user['username']}':\n\n    {pwd}\n")
    print("Đăng nhập bằng mật khẩu này, hệ thống sẽ bắt đặt mật khẩu mới ngay." + (" MFA đã được gỡ — hãy cài lại." if args.reset_mfa else ""))
    return 0


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)  # chỉ khi chạy từ dòng lệnh: không in log khởi tạo CSDL lẫn với mật khẩu tạm
    sys.exit(main())
