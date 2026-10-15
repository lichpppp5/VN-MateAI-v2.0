# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""AI tự chọn đúng công cụ MCP tham chiếu khi được hỏi bằng tiếng Việt — LLM THẬT, chỉ chạy khi VNMATEAI_LIVE_LLM=1:

    VNMATEAI_LIVE_LLM=1 python -m pytest -q tests/test_mcp_reference_live.py -s

Cần đã chạy `python scripts/setup_mcp_servers.py` (đọc khai báo server thật ở config/mcp_servers.json; CSDL vẫn cách ly)."""
from __future__ import annotations

import os

import pytest

from mateai.application.agent_gateway import mcp_gateway as gw
from mateai.config.loader import settings

pytestmark = pytest.mark.skipif(os.environ.get("VNMATEAI_LIVE_LLM") != "1", reason="cần VNMATEAI_LIVE_LLM=1 (gọi LLM thật)")


@pytest.mark.parametrize("question,tool,must", [
    ("Bây giờ ở New York là mấy giờ?", "mcp_time_get_current_time", None),
    ("9 giờ sáng ở Hà Nội là mấy giờ ở Tokyo?", "mcp_time_convert_time", "11"),
    ("Đọc trang https://example.com và cho tôi biết tiêu đề trang là gì", "mcp_fetch_fetch", "Example Domain"),
])
async def test_ai_picks_the_reference_tool(question, tool, must, monkeypatch):
    monkeypatch.setattr(settings.agent_gateway, "allow_stdio", True)
    assert gw.register_all_from_cache() >= 3, "chưa đăng ký MCP server tham chiếu"
    from mateai.application.agent.llm_engine import llm_engine
    out = await llm_engine.ask_async(question, source_device="portal", caller="admin")
    called = [c.get("skill") for c in out.get("tool_calls_made") or []]
    print(f"\n{question}\n  công cụ: {called}\n  trả lời: {out.get('reply', '')[:300]}")
    try:
        assert tool in called, called
        if must:
            assert must in out["reply"], out["reply"]
    finally:
        for sid in list(gw._clients):
            await gw._clients.pop(sid).close()
