# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Mọi mẫu khai báo phải qua bước chuẩn hoá của sổ đăng ký (tức dùng được ngay khi điền địa chỉ + khoá)."""
from __future__ import annotations

import pytest

from mateai.infrastructure.connectors import custom_registry as reg
from mateai.infrastructure.connectors import presets


@pytest.mark.parametrize("preset", presets.catalog(), ids=lambda p: p["key"])
def test_every_preset_is_a_valid_declaration(preset):
    decl = {**preset["declaration"], "base_url": "https://may-chu.congty.local/api", "auth_value": "khoa-thu"}
    rec = reg._normalise_source("mau-" + preset["key"], decl, None)
    assert rec["queries"] or rec["actions"]
    for action in rec["actions"].values():
        assert action["risk_level"] >= 3                     # can thiệp luôn cần duyệt
    assert rec["default_path"] in rec["queries"] or rec["default_path"].startswith("/")


def test_presets_are_honest_about_not_being_verified_on_real_systems():
    assert all(p["verified"] is False for p in presets.catalog())
    assert len({p["key"] for p in presets.catalog()}) == len(presets.PRESETS)
