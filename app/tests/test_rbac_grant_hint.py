# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Lỗi "không có quyền" phải chỉ đúng chỗ sửa: robot token riêng → đặt quyền thiết bị; robot token chung / tài khoản quyền thấp → cấp token
riêng hoặc đăng nhập tài khoản cao hơn. Thông báo không được nâng quyền gì — chỉ hướng dẫn.
"""
from mateai.application.security import security_guard as sg


def test_device_with_own_token_is_told_where_to_set_its_role():
    ok, reason = sg.security_guard._evaluate_rbac("run_powershell_command", "viewer", "device:robot-phong-khach")
    assert not ok and "robot-phong-khach" in reason and "/api/v1/security/devices/robot-phong-khach/role" in reason


def test_shared_token_robot_or_low_account_is_told_to_get_its_own_token_or_log_in():
    ok, reason = sg.security_guard._evaluate_rbac("run_powershell_command", "viewer", "esp32-abc")
    assert not ok and "token CHUNG" in reason and "đăng nhập" in reason


def test_admin_is_still_allowed_everything_by_rbac():
    assert sg.security_guard._evaluate_rbac("run_powershell_command", "admin", "device:robot-1")[0]
