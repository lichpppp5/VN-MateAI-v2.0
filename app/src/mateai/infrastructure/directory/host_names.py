# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/directory/host_names.py
=============================================
Chuẩn hoá tên máy để so khớp giữa AD, ERP và Agent: `PC-KT-01.corp.local` = `pc-kt-01` = `PC-KT-01$`.
Một bản duy nhất — `workstation_directory` (tầng application) và `erp_database` (nhập từ AD) cùng dùng.
Địa chỉ IP (chỉ chữ số và dấu chấm) giữ nguyên, không cắt theo dấu chấm.
"""
from __future__ import annotations

from typing import Any


def norm_host(name: Any) -> str:
    s = str(name or "").strip().lower().rstrip("$")
    if not s or s.replace(".", "").isdigit():
        return s
    return s.split(".", 1)[0]
