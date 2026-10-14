# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase87_hud_suy_nghi_model.py
========================================
Phase 87 — hiện "QUÁ TRÌNH SUY NGHĨ" của model trên HUD 3D (Ly Ly).

Sự thật đo được trước khi làm (2026-09-30, máy này)
----------------------------------------------------
Model đang bật `VN-MateAi` (combo của router cục bộ, `reasoning: true`,
`thinkingFormat: null`). Gọi thật tới router:

  * câu "12 quả táo cho đi 3 rồi mua thêm 5" sinh khoảng 250-700 ký tự suy
    nghĩ, và `usage.completion_tokens_details.reasoning_tokens` = 253
  * suy nghĩ nằm ở field `reasoning` (stream: `delta.reasoning`), TÁCH HẲN
    khỏi `content` — nên câu trả lời bạn nghe không bị lẫn suy nghĩ
  * lệnh `extra_body={"thinking": {"budget_tokens": 0}}` trong code KHÔNG có
    tác dụng: token suy nghĩ vẫn bị tính

Hậu quả trước Phase 87: suy nghĩ được sinh ra, tốn tiền, rồi bị vứt đi —
không chỗ nào đọc. Người dùng chọn: hiện trên HUD, giữ nguyên tiếng Anh,
chỉ làm gọn.

Cách kiểm tra
-------------
1. `_compact_reasoning()` — hàm thuần, kiểm tra trực tiếp: gộp khoảng trắng,
   cắt ở ranh giới từ, không bỏ từ nào (bỏ từ = bịa).
2. `stream_voice_response()` với client giả lập — chứng minh suy nghĩ được
   gom vào `last_voice_reasoning` mà KHÔNG lẫn vào câu trả lời.
3. `ask_async()` với `_call_llm` giả lập — chứng minh kết quả trả về có
   kèm `reasoning`, và `content` sạch.
4. HUD: khung tồn tại, gập lại được, dùng `textContent` (không innerHTML).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))
        if "pytest" in sys.modules:   # dưới pytest trượt là ĐỎ (không có test giả)
            raise AssertionError(f"{name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. _compact_reasoning — hàm thuần
# ─────────────────────────────────────────────────────────────────────────────
def test_compact_reasoning() -> None:
    from mateai.application.agent.llm_engine import LLMEngine

    section("_compact_reasoning: gọn mà không bỏ nội dung")

    check("chuỗi rỗng -> rỗng", LLMEngine._compact_reasoning("") == "")
    check("None -> rỗng", LLMEngine._compact_reasoning(None) == "")  # type: ignore[arg-type]

    one = "Còn 14 quả táo."
    check("ngắn không bị cắt", LLMEngine._compact_reasoning(one) == one)

    # Suy nghĩ thật của model: nhiều dòng, thừa khoảng trắng, xuống dòng giữa
    # câu. Phải gộp thành một dòng.
    messy = "We need to answer\n   in Vietnamese, short.\n\n12 - 3 = 9,\n+ 5 = 14."
    flat = LLMEngine._compact_reasoning(messy)
    check("gộp nhiều dòng thành một dòng", "\n" not in flat, repr(flat))
    check("gộp khoảng trắng thừa", "  " not in flat, repr(flat))
    check("giữ nguyên nội dung từng từ",
          all(w in flat for w in ("We", "need", "answer", "Vietnamese", "12", "9", "14")),
          repr(flat))

    # Cắt dài: phải cắt ở ranh giới từ, không dính nửa từ, và có dấu "…".
    long_text = "word " * 400  # 2000 ký tự
    cut = LLMEngine._compact_reasoning(long_text)
    check("cắt xuống dưới giới hạn", len(cut) <= LLMEngine.REASONING_DISPLAY_MAX + 1,
          f"{len(cut)} ký tự")
    check("có dấu cắt", cut.endswith("…"), repr(cut[-12:]))
    check("không cắt giữa từ", not cut.rstrip("…").endswith("wor"), repr(cut[-12:]))

    # Giới hạn tùy chọn phải thắng giới hạn mặc định.
    check("max_len tùy chọn",
          len(LLMEngine._compact_reasoning(long_text, max_len=50)) <= 51)

    # KHÔNG được dịch/tóm tắt: mọi từ gốc phải còn nguyên trong bản ngắn.
    short = "Tôi cần trả lời bằng tiếng Việt. 12 trừ 3 bằng 9, cộng 5 bằng 14."
    check("không bỏ từ khi ngắn", LLMEngine._compact_reasoning(short) == short)

    # Ký tự nguy hiểm phải được giữ nguyên (không tự sửa, không bỏ).
    tricky = "Giá trị <script>alert(1)</script> & 'x' \"y\""
    check("giữ nguyên ký tự đặc biệt",
          LLMEngine._compact_reasoning(tricky) == tricky, repr(LLMEngine._compact_reasoning(tricky)))


# ─────────────────────────────────────────────────────────────────────────────
# 2. stream_voice_response — gom suy nghĩ, không lẫn vào câu trả lời
# ─────────────────────────────────────────────────────────────────────────────
class _FakeDelta:
    def __init__(self, content: str = "", reasoning: str = "") -> None:
        self.content = content
        self.reasoning = reasoning
        self.reasoning_content = ""
        self.role = "assistant"
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, delta) -> None:
        self.delta = delta
        self.finish_reason = None


class _FakeChunk:
    def __init__(self, delta) -> None:
        self.choices = [_FakeChoice(delta)]


class _FakeCompletions:
    def __init__(self, chunks) -> None:
        self._chunks = chunks

    async def create(self, **_kwargs):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


class _FakeClient:
    def __init__(self, chunks) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(chunks))


def _install_fake_client(llm_engine, chunks) -> None:
    """Thay client thật bằng client giả lập, và bỏ qua khởi tạo client.

    Đặt tên model cố định: với config mẫu (CI) model là `YOUR_MODEL_NAME_HERE`,
    provider coi là chưa cấu hình và không gọi client — test sẽ phụ thuộc
    config.json của máy chạy."""
    from mateai.config.loader import settings
    settings.llm.model_name = "test/fake-model"
    settings.llm.router_models = ["test/fake-model"]
    llm_engine._client = _FakeClient(chunks)  # type: ignore[assignment]
    llm_engine._ensure_shared_client = _noop_ensure  # type: ignore[method-assign]


async def _noop_ensure() -> None:
    return None


def test_stream_reasoning() -> None:
    from mateai.application.agent.llm_engine import llm_engine

    section("stream_voice_response: gom suy nghĩ, giữ câu trả lời sạch")

    # Mô phỏng đúng thứ tự của model thật: suy nghĩ trước (nhiều chunk), rồi
    # mới viết câu trả lời.
    chunks = [
        _FakeChunk(_FakeDelta(reasoning="We need to answer")),
        _FakeChunk(_FakeDelta(reasoning=" in Vietnamese.")),
        _FakeChunk(_FakeDelta(reasoning=" 12 - 3 = 9, + 5 = 14.")),
        _FakeChunk(_FakeDelta(content="Còn 14")),
        _FakeChunk(_FakeDelta(content=" quả táo.")),
    ]
    _install_fake_client(llm_engine, chunks)
    llm_engine.last_voice_reasoning = "SUY NGHĨ CŦ LƯỢT TRƯỚC"  # type: ignore[misc]

    async def _run():
        out = []
        async for s in llm_engine.stream_voice_response(
            query="12 quả táo cho đi 3 rồi mua thêm 5?",
            source_device="hud",
        ):
            out.append(s)
        return out

    sentences = asyncio.run(_run())

    check("câu trả lời đúng", sentences == ["Còn 14 quả táo."], str(sentences))
    check("suy nghĩ được gom",
          "12 - 3 = 9" in llm_engine.last_voice_reasoning,
          repr(llm_engine.last_voice_reasoning))
    check("suy nghĩ KHÔNG lẫn vào câu trả lời",
          all("We need" not in s and "Vietnamese" not in s for s in sentences),
          str(sentences))
    check("suy nghĩ của lượt cũ đã bị xoá",
          "LƯỢT TRƯỚC" not in llm_engine.last_voice_reasoning,
          repr(llm_engine.last_voice_reasoning))

    # Lượt không có suy nghĩ -> phải rỗng, không hiện lại lượt trước.
    _install_fake_client(llm_engine, [_FakeChunk(_FakeDelta(content="Dạ."))])
    asyncio.run(_run())
    check("lượt không suy nghĩ -> rỗng", llm_engine.last_voice_reasoning == "",
          repr(llm_engine.last_voice_reasoning))


# ─────────────────────────────────────────────────────────────────────────────
# 3. ask_async — đường không streaming (REST /api/v1/voice-command)
# ─────────────────────────────────────────────────────────────────────────────
def _fake_message(content: str, reasoning: str):
    return SimpleNamespace(
        content=content,
        reasoning=reasoning,
        reasoning_content="",
        tool_calls=None,
    )


def _fake_response(content: str, reasoning: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=_fake_message(content, reasoning),
            finish_reason="stop",
        )],
    )


def test_agentic_reasoning() -> None:
    from mateai.application.agent.llm_engine import llm_engine

    section("ask_async: kết quả kèm reasoning, content sạch")

    # Giả lập mọi ngoại lệ để vòng agentic chạy tới nhánh "trả lời thẳng".
    llm_engine._call_llm = _fake_call_llm  # type: ignore[method-assign]

    import core.plugin_manager as pm
    import mateai.application.skills.plugin_registry as pr
    import mateai.application.conversation.memory_manager as mm

    pm.plugin_manager.get_all_tools = lambda: []  # type: ignore[assignment]
    pr.plugin_registry.get_all_tools_schema = lambda: []  # type: ignore[assignment]
    mm.memory_manager.add_turn = lambda *a, **k: None  # type: ignore[assignment]

    async def _run():
        return await llm_engine.ask_async(
            query="12 quả táo cho đi 3 rồi mua thêm 5?",
            source_device="hud",
            session_id="hud",
        )

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # pylint: disable=broad-except
        check("ask_async chạy được", False, f"{type(exc).__name__}: {exc}")
        return

    check("có key reasoning trong kết quả", "reasoning" in result, str(sorted(result)))
    check("reasoning đúng nội dung",
          "12 - 3 = 9" in (result.get("reasoning") or ""),
          repr(result.get("reasoning")))
    check("content sạch, không lẫn suy nghĩ",
          "We need" not in (result.get("reply") or ""),
          repr(result.get("reply")))
    check("success", result.get("success") is True, str(result.get("success")))


async def _fake_call_llm(*_a, **_k):
    return _fake_response(
        "Còn 14 quả táo.",
        "We need to answer in Vietnamese. 12 - 3 = 9, + 5 = 14.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. HUD: khung tồn tại, gập lại, dùng textContent
# ─────────────────────────────────────────────────────────────────────────────
def test_hud_panel() -> None:
    section("HUD: khung suy nghĩ tồn tại và an toàn")

    html = Path("web/hud.html").read_text(encoding="utf-8")
    js = Path("web/hud.js").read_text(encoding="utf-8")

    for eid in ("hud-thinking", "hud-thinking-head", "hud-thinking-dot",
                "hud-thinking-peek", "hud-thinking-caret",
                "hud-thinking-body", "hud-thinking-text"):
        check(f"HTML có #{eid}", f'id="{eid}"' in html)

    check("khung mặc định ẩn", 'id="hud-thinking" class="hidden' in html)
    check("thân khung mặc định ẩn", 'id="hud-thinking-body" class="hidden' in html)
    check("có nút gập/mở", "function toggleHudThinking()" in js)
    check("có hàm setHudThinking", "function setHudThinking(state" in js)
    # Phải gán ra window: onclick trong HTML chạy ở global scope, không thấy
    # hàm trong IIFE. showHudDisplayCard cũng làm vậy (dòng 1056).
    check("toggleHudThinking gán ra window",
          "window.toggleHudThinking = toggleHudThinking" in js)
    check("setHudThinking gán ra window",
          "window.setHudThinking = setHudThinking" in js)

    # An toàn: suy nghĩ là văn bản model sinh ra, phải dùng textContent.
    # Dùng innerHTML ở đây là mỗi lượt hỏi một lỗ hổng chèn mã.
    check("dùng textContent cho nội dung suy nghĩ",
          "thinkingTextEl && (thinkingTextEl.textContent = text)" in js)
    check("dùng textContent cho dòng tóm tắt",
          "thinkingPeekEl && (thinkingPeekEl.textContent" in js)
    check("KHÔNG dùng innerHTML cho suy nghĩ",
          "thinkingTextEl.innerHTML" not in js and "thinkingPeekEl.innerHTML" not in js)

    # Bộ đếm chốt chặn: máy chủ đứt giữa chừng thì vòng xoay phải tự tắt.
    check("có bộ đếm chốt chặn", "THINKING_STUCK_MS" in js)
    check("bộ đếm gọi setHudThinking('empty')",
          "setHudThinking('empty')" in js)

    # Bộ điều phối tin nhắn phải nhận gói "thinking".
    check("onmessage nhận gói thinking", "type === 'thinking'" in js)
    check("chuyển status sang setHudThinking",
          "setHudThinking(packet.status" in js)

    # Cả 3 trạng thái đều được xử lý.
    for st in ("'thinking'", "'done'", "'empty'"):
        check(f"xử lý status {st}", st in js)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Backend: mọi đường voice đều bắn gói thinking
# ─────────────────────────────────────────────────────────────────────────────
def test_server_wiring() -> None:
    section("server.py: mọi đường voice đều bắn gói thinking")

    # Luồng HUD nằm ở hud_voice.py, đường REST ở routers/voice.py.
    src = Path("src/mateai/interfaces/http/hud_voice.py").read_text(encoding="utf-8")
    rest_src = Path("src/mateai/interfaces/http/routers/voice.py").read_text(encoding="utf-8")

    check("có hàm broadcast_thinking", "async def broadcast_thinking(" in src)
    check("gói có type thinking", '"type": "thinking"' in src)
    check("gói có status", '"status": state' in src)
    check("gói có text", '"text": text' in src)

    # Đường WS (voice qua microphone HUD)
    # Phase 3: phần riêng của HUD gồm đầu ra _HudVoiceSink + thân hàm.
    _start = src.index("class HudVoiceSink")
    body = src[_start:src.index("def get_metrics_payload")]
    check("đường WS: báo 'thinking' khi nhận lệnh",
          'broadcast_thinking("thinking"' in body)
    check("đường WS: báo 'done' khi có câu trả lời",
          'broadcast_thinking(\n                    "done"' in body
          or 'broadcast_thinking("done"' in body
          or '"done",' in body
          or '"done" if reasoning' in body)
    check("đường WS: báo 'empty' khi lỗi",
          'broadcast_thinking("empty"' in body)

    # Đường REST (/api/v1/voice-command)
    rest = rest_src[rest_src.index('"/api/v1/voice-command"'):]
    check("đường REST: báo 'thinking'", 'broadcast_thinking("thinking"' in rest)
    check("đường REST: báo 'done'/'empty'",
          'broadcast_thinking(' in rest and '"done"' in rest and '"empty"' in rest)

    # Không được bắn suy nghĩ vào câu trả lời (sẽ bị đọc to).
    check("suy nghĩ KHÔNG nằm trong text của voice_active",
          "last_voice_reasoning" not in body.split('"type": "voice_active"')[1].split("}")[0]
          if '"type": "voice_active"' in body else True)


def main() -> None:
    test_compact_reasoning()
    test_stream_reasoning()
    test_agentic_reasoning()
    test_hud_panel()
    test_server_wiring()

    print("\n" + "=" * 62)
    if FAILED:
        print("SAI:")
        for f in FAILURES:
            print(f)
    print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
    print("=" * 62)
    if FAILED:
        sys.exit(1)
    print("\n✅ TẤT CẢ PASS")


if __name__ == "__main__":
    main()
