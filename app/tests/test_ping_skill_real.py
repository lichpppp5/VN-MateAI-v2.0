# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_ping_skill_real.py
=============================
`test_ping_host` (skills/custom_skills.py) đo THẬT: trước đây trả cứng {"ping": "15ms"} và không
nhận địa chỉ — LLM thật (scripts/eval_llm.py, 2026-10-06) né nó và chọn PowerShell để ping.

  - địa chỉ bắt buộc + kiểm tra (không chuỗi lệnh, không cờ "-x");
  - lệnh chạy dạng danh sách đối số (không shell);
  - kết quả lấy từ dòng trả lời có TTL (mọi ngôn ngữ hệ điều hành).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import skills.custom_skills as cs  # noqa: E402

WIN_OUT = (b"Pinging 8.8.8.8 with 32 bytes of data:\r\n"
           b"Reply from 8.8.8.8: bytes=32 time=38ms TTL=115\r\n"
           b"Reply from 8.8.8.8: bytes=32 time=41ms TTL=115\r\n"
           b"Request timed out.\r\n")


def test_parses_real_replies_without_shell(monkeypatch):
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout=WIN_OUT, stderr=b"")

    monkeypatch.setattr(cs.subprocess, "run", fake_run)
    res = cs.test_ping_host(host="8.8.8.8", count=3)
    assert seen["args"][0] == "ping" and seen["args"][-1] == "8.8.8.8" and "shell" not in seen["kw"]
    assert res == {"status": "success", "host": "8.8.8.8", "sent": 3, "received": 2, "loss_percent": 33.3,
                   "min_ms": 38.0, "avg_ms": 39.5, "max_ms": 41.0}


def test_unreachable_and_invalid_hosts(monkeypatch):
    monkeypatch.setattr(cs.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, stdout=b"Request timed out.\r\n", stderr=b""))
    assert cs.test_ping_host(host="192.0.2.1", count=1)["status"] == "unreachable"
    for bad in ("", "-n 1 x", "8.8.8.8 & calc", "a;b", "host|x"):
        assert cs.test_ping_host(host=bad)["status"] == "error"


def test_no_more_fixed_number():
    assert "15ms" not in (ROOT / "skills" / "custom_skills.py").read_text(encoding="utf-8")
