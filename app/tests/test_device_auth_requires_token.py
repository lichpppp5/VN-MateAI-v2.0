# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_device_auth_requires_token.py
========================================
WebSocket thiết bị (/api/v1/xiaozhi/ws, /ws/audio-stream) bắt buộc device token.

Trước đây có nhánh "Zero-Config LAN": mọi IP nội bộ được nhận KHÔNG cần token,
device_id tự đặt trên URL, id esp32*/xiaozhi* được quyền admin → mọi máy trong
LAN ra lệnh được với quyền admin. (Chủ dự án chọn: bắt buộc token.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.interfaces.http.server as server  # noqa: E402
from mateai.interfaces.http import ws_auth  # noqa: E402
from mateai.interfaces.http import enrollment  # noqa: E402


def _ws(ip, token=None, header=None):
    q = {"token": token} if token else {}
    h = {"authorization": header} if header else {}
    return SimpleNamespace(query_params=q, headers=h, client=SimpleNamespace(host=ip))


@pytest.mark.parametrize("ip", ["192.168.1.50", "10.0.0.7", "172.20.1.2", "127.0.0.1"])
def test_lan_device_without_token_is_rejected(ip):
    assert ws_auth.authenticate_device(_ws(ip)) is False


def test_device_secret_accepted_via_query_or_bearer(monkeypatch):
    monkeypatch.setattr(enrollment, "get_device_enrollment_secret", lambda: "dev-secret-123")
    # Chế độ tương thích (token chung còn nhận) đặt TƯỜNG MINH — không phụ thuộc config.json của máy.
    monkeypatch.setattr("mateai.config.loader.get_config_section",
                        lambda name: {"require_per_device_token": False} if name == "security" else {})
    assert ws_auth.authenticate_device(_ws("192.168.1.50", token="dev-secret-123")) is True
    assert ws_auth.authenticate_device(_ws("8.8.8.8", header="Bearer dev-secret-123")) is True
    assert ws_auth.authenticate_device(_ws("192.168.1.50", token="wrong")) is False
