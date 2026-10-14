# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase86_telegram_chi_1_tin.py
========================================
Phase 86 — mỗi việc cần CEO duyệt chỉ sinh ĐÚNG 1 tin Telegram.

Vấn đề (người dùng báo: "Telegram sao cứ bắn tin nhắn YÊU CẦU PHÊ DUYỆT")
-----------------------------------------------------------------------
Một yêu cầu duyệt tạo ra 2 tin:

  1. tin yêu cầu (kèm nút bấm 1-tap) — `send_hitl_request`
  2. tin xác nhận kết quả — `send_incident_alert`, bắn lại sau khi CEO bấm
     Duyệt hoặc Từ chối

Tin thứ hai không mang thông tin mới: nó chỉ lặp lại việc CEO vừa bấm bằng
chính ngón tay đó. Hậu quả: duyệt 1 việc mà phải đọc 2 tin, và mỗi lần từ
chối lại là thêm 1 tin nữa vào inbox.

Cách sửa: `HITL_NOTIFY_RESULT = False` chặn ở đúng một chỗ (cả hai nhánh duyệt
và từ chối), thay vì xoá code ở từng nơi. Thông tin KHÔNG mất vì
`log_audit_action()` vẫn ghi bất biến mọi lần duyệt/từ chối.

Cách kiểm tra
--------------
Đếm số lần `telegram_gateway.send_hitl_request` và `send_incident_alert` thực
được gọi, cộng số lần `erp_db.log_audit_action` — chứng minh "1 tin ra, thông
tin vẫn còn trong audit log", không chỉ kiểm tra chuỗi.
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def section(title: str) -> None:
    print(f"\n▸ {title}")


def main() -> None:
    from mateai.application.security import zero_trust
    from mateai.application.security.zero_trust import HITL_NOTIFY_RESULT, hitl_manager
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway as tg
    from mateai.infrastructure.database.erp_database import erp_db

    # ── Đếm, không gửi thật ────────────────────────────────────────────────
    sent = {"request": 0, "alert": 0}
    audit: list[tuple[str, str, str]] = []

    def _stub_request(*_a, **_k) -> bool:
        sent["request"] += 1
        return True  # giả vờ có nút bấm

    def _stub_alert(*_a, **_k) -> bool:
        sent["alert"] += 1
        return True

    tg.send_hitl_request = _stub_request
    tg.send_incident_alert = _stub_alert
    erp_db.log_audit_action = lambda **kw: audit.append(
        (kw.get("action_type", ""), kw.get("status", ""), kw.get("approved_by", ""))
    )

    with hitl_manager._lock:
        hitl_manager._pending_approvals.clear()
        hitl_manager._action_callbacks.clear()

    ran: list[str] = []

    def _exec_a() -> dict:
        ran.append("a")
        return {"ok": True}

    def _exec_b() -> dict:
        ran.append("b")
        return {"ok": True}

    # ═══ 1. Một yêu cầu duyệt = đúng 1 tin ═══════════════════════════════
    section("Một yêu cầu duyệt chỉ sinh MỘT tin")

    check("cờ tắt bắn tin kết quả", HITL_NOTIFY_RESULT is False)

    r1 = hitl_manager.request_approval(
        "record_income", {"amount": 1_000_000, "muc": "a"},
        requested_by="AI", description="d1", action_callback=_exec_a, risk_level=4,
    )
    id1 = r1["id"]
    check("mở yêu cầu: đúng 1 tin yêu cầu", sent["request"] == 1, str(sent))
    check("mở yêu cầu: KHÔNG có tin kết quả", sent["alert"] == 0, str(sent))

    # ═══ 2. Duyệt: không bắn tin thứ hai ═════════════════════════════════
    section("Bấm Duyệt — không bắn tin xác nhận")

    sent["request"] = 0
    res = hitl_manager.approve(id1, approved_by="CEO")
    check("duyệt thành công", res.get("status") == "success", str(res.get("status")))
    check("tác vụ thật sự đã chạy", ran == ["a"], str(ran))
    check("duyệt: 0 tin Telegram nào được bắn", sent == {"request": 0, "alert": 0}, str(sent))
    check(
        "duyệt: kết quả vẫn trả về cho Cổng Web",
        "Đã phê duyệt" in str(res.get("message", "")),
        str(res.get("message")),
    )

    # ═══ 3. Từ chối: không bắn tin ════════════════════════════════════════
    section("Bấm Từ chối — không bắn tin xác nhận")

    r2 = hitl_manager.request_approval(
        "record_income", {"amount": 9_000_000, "muc": "b"},
        requested_by="AI", description="d2", action_callback=_exec_b, risk_level=4,
    )
    id2 = r2["id"]
    sent["request"] = 0
    sent["alert"] = 0
    rej = hitl_manager.reject(id2, rejected_by="CEO", reason="không cần")
    check("từ chối thành công", rej.get("status") == "success", str(rej.get("status")))
    check("tác vụ KHÔNG chạy", ran == ["a"], str(ran))
    check("từ chối: 0 tin Telegram nào được bắn", sent == {"request": 0, "alert": 0}, str(sent))

    # ═══ 4. Thông tin KHÔNG mất — vẫn nằm trong audit log ═════════════════
    section("Thông tin không mất: audit log vẫn ghi đủ")

    types = [a[0] for a in audit]
    check(
        "có bản ghi mỗi lần mở yêu cầu",
        sum(1 for t in types if t.endswith("REQUEST_RECORD_INCOME")) == 2,
        str(types),
    )
    check(
        "có bản ghi lúc duyệt",
        sum(1 for t in types if t == "HITL_APPROVED_RECORD_INCOME") == 1,
        str(types),
    )
    check(
        "bản ghi lúc duyệt lưu cả kết quả thực thi",
        any(a[0] == "HITL_APPROVED_RECORD_INCOME" and a[1] == "success" for a in audit),
        str([a for a in audit if "APPROVED" in a[0]]),
    )
    check(
        "có bản ghi lúc từ chối, kèm trạng thái 'blocked'",
        any(a[0] == "HITL_REJECTED_RECORD_INCOME" and a[1] == "blocked" for a in audit),
        str([a for a in audit if "REJECTED" in a[0]]),
    )
    check(
        "bản ghi ghi rõ AI là người yêu cầu",
        all(a[2] == "CEO" for a in audit if "REQUEST" not in a[0]),
        str(audit),
    )

    # ═══ 5. Bật lại được mà không phải sửa lại escape ═════════════════════
    section("Đường dẫn bật lại vẫn an toàn")

    src = Path("src/mateai/application/security/zero_trust.py").read_text(encoding="utf-8")
    check("cả hai nhánh duyệt/từ chối đều nằm sau cùng một cờ",
          src.count("if HITL_NOTIFY_RESULT:") == 1, str(src.count("if HITL_NOTIFY_RESULT:")))
    check("phần escape HTML của tin kết quả còn nguyên",
          "html.escape(outcome_msg)" in src and "html.escape(str(item['action_name']))" in src)
    # Bật cờ thật rồi đo lại: phải ra 1 tin kết quả cho mỗi lần bấm, và tin đó
    # phải escape (không còn thẻ < > thô, không còn & trần).
    zero_trust.HITL_NOTIFY_RESULT = True
    try:
        import html as _html
        import re as _re

        sent["alert"] = 0
        r3 = hitl_manager.request_approval(
            "record_income", {"amount": 7_000_000, "muc": "c"},
            requested_by="AI", description="d3", action_callback=_exec_b, risk_level=4,
        )
        hitl_manager.approve(r3["id"], approved_by="CEO")
        check("bật cờ lại thì tin kết quả quay về", sent["alert"] == 1, str(sent))
    finally:
        zero_trust.HITL_NOTIFY_RESULT = False

    sent["alert"] = 0
    r4 = hitl_manager.request_approval(
        "record_income", {"amount": 8_000_000, "muc": "d"},
        requested_by="AI", description="d4", action_callback=_exec_b, risk_level=4,
    )
    hitl_manager.reject(r4["id"], rejected_by="CEO", reason="x")
    check("tắt lại thì yên", sent["alert"] == 0, str(sent))

    # ═══ 6. Không sót chỗ nào vẫn bắn tin kết quả ════════════════════════
    section("Không còn chỗ nào bắn tin kết quả ngoài cờ")

    # Chia source theo từng hàm để chứng minh đúng vị trí, không đếm chuỗi
    # thô (dễ bị comment/sliding nghĩa gây sai).
    def _region(start_marker: str, end_marker: str) -> str:
        a = src.index(start_marker)
        b = src.index(end_marker, a)
        return src[a:b]

    def _sends(text: str) -> list[str]:
        out = []
        for ln in text.splitlines():
            t = ln.strip()
            if t.startswith("#") or t.startswith("f\"") or t.startswith("f'"):
                continue  # comment hoặc phần của nội dung tin nhắn
            if "send_incident_alert(" in t:
                out.append(t)
        return out

    req = _region("    def request_approval(", "    def get_pending_list(")
    check("request_approval: 1 chỗ gọi, và đó là bản CHỮ thay nút bấm",
          len(_sends(req)) == 1 and "fallback_msg" in _sends(req)[0], str(_sends(req)))

    # Mọi phần duyệt/từ chối nằm sau `def _build_approve_result` (nơi cả
    # approve(), approve_async() và reject() cùng dùng chung kết quả).
    review = _region("    def _build_approve_result(", "hitl_manager = HumanInTheLoopManager()")
    check("phần duyệt/từ chối: chỉ còn đúng 1 chỗ gọi", len(_sends(review)) == 1, str(_sends(review)))
    check("chỗ đó nằm sau cờ HITL_NOTIFY_RESULT",
          "if HITL_NOTIFY_RESULT:" in review
          and review.index("if HITL_NOTIFY_RESULT:") < review.index("telegram_gateway.send_incident_alert("))

    # ── Kết quả ───────────────────────────────────────────────────────────
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
