# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/architecture/test_http_timeouts.py
========================================
Mọi lời gọi HTTP ra ngoài phải có timeout.

`httpx` mặc định 5 s nhưng `requests` và `urllib.request.urlopen` mặc định
KHÔNG có giới hạn: một connector/LLM/Telegram treo là giữ một luồng (hoặc cả
event loop, nếu gọi nhầm trên loop) mãi mãi. Hiện cả 25 chỗ đều đặt timeout
tường minh — test này giữ nguyên trạng đó cho code mới.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GLOBS = ["src/**/*.py", "core/*.py", "skills/*.py", "workers/*.py", "main.py"]
CALLS = {
    "httpx.Client", "httpx.AsyncClient", "aiohttp.ClientSession",
    "requests.get", "requests.post", "requests.put", "requests.delete", "requests.request",
    "urllib.request.urlopen",
}


def _missing_timeout() -> list[str]:
    out = []
    for pattern in GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = ast.unparse(node.func)
                if name not in CALLS:
                    continue
                has_timeout = any(k.arg == "timeout" for k in node.keywords) or (
                    name == "urllib.request.urlopen" and len(node.args) >= 3)
                if not has_timeout:
                    out.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno} {name}")
    return out


def test_every_outbound_http_call_has_a_timeout():
    missing = _missing_timeout()
    assert not missing, "Lời gọi HTTP không có timeout:\n  " + "\n  ".join(missing)
