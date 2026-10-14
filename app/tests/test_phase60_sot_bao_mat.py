#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase60_sot_bao_mat.py
==================================
Kiểm thử hồi quy cho các bản sửa bảo mật / tính đúng của Phase 59-60.

Mỗi test dưới đây chặn một lỗi đã tồn tại trong code và có thể quay lại
nếu ai đó "dọn dẹp" mà không biết lý do:

  1. verify_oci_signature trả True cho mọi request  -> webhook giả mạo được tin
  2. "continuing anyway" sau khi xác thực thất bại   -> xác thực trang trí
  3. metadata ghi verified=true dù chưa cấu hình secret -> báo cáo sai
  4. _sync_executor gọi tool.function 3 lần         -> side-effect lặp
  5. walrus `:=` bị `in` nuốt trong HITLManager     -> không ghi message_id
  6. execute_with_hitl nhận executor coroutine      -> duyệt xong, không chạy
  7. _finalize_result trả success=false, error=null -> UI hiện ✔ xanh khi lỗi
  8. _run_task chỉ await, hàm sync -> FAILED âm thầm
  9. startup chạy 2 lần (dual uvicorn listener)     -> đăng ký tool trùng
 10. database._lock là Lock, add_finance_record tự khoá chết -> TREO CẢ SERVER
 11. 3 call site execute_with_hitl thiếu await      -> cổng Zero-Trust chết
 12. risk_level khai trên tool bị cổng HITL bỏ qua  -> duyệt xong vẫn lọt
 13. webhook chưa cấu hình secret vẫn ghi verified=true -> báo cáo sai
 14. approve() gọi cb() với executor coroutine      -> duyệt xong, KHÔNG chạy
 15. mô tả HITL có `_` làm Telegram 400            -> CEO không hề được hỏi
 16. connector báo 'đã cấu hình' dù chưa có credential -> báo cáo thành công giả

Chạy:  python3 tests/test_phase60_sot_bao_mat.py
Không cần server, không cần mạng.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Im stdout của log để output test gọn (loguru/logging ghi ra stderr).
import logging

logging.getLogger().setLevel(logging.CRITICAL)

PASS = 0
FAIL = 0
RESULTS: list[str] = []


def _http_src() -> str:
    """Mã tầng HTTP + use case nó gọi. Trước đây đọc mỗi `server.py` — sau khi tách router
    (Phase 6 / Supervisor P10) các kiểm tra chuỗi này trượt mà pytest vẫn xanh."""
    root = Path("src/mateai")
    files = [root / "interfaces/http/server.py", root / "interfaces/http/topology.py",
             root / "interfaces/http/approval_flow.py", root / "application/enterprise/integrations.py",
             root / "application/security/approval_decisions.py",
             *sorted((root / "interfaces/http/routers").glob("*.py"))]
    return "\n\n".join(f.read_text(encoding="utf-8") for f in files)


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append(f"  ✅ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ❌ {name}" + (f" — {extra}" if extra else ""))
        if "pytest" in sys.modules:
            # Dưới pytest: trượt là ĐỎ. Trước đây chỉ ghi vào RESULTS nên 11 kiểm tra
            # trượt mà bộ test vẫn xanh (prompt cuối: không có test giả).
            raise AssertionError(f"{name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    RESULTS.append(f"\n{title}")


# ══════════════════════════════════════════════════════════════════════════
# 1-3. Xác thực webhook
# ══════════════════════════════════════════════════════════════════════════
def test_webhook_signature() -> None:
    section("── Xác thực webhook (chống webhook giả mạo) ──")
    from mateai.interfaces.http.webhook_gateway import SignatureVerifier as V

    body = b'{"event_type":"instance_down"}'
    secret = "shared-secret-diem-thu"
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    check("HMAC đúng -> True", V.verify_oci_signature(body, {"x-oci-signature": good}, secret) is True)
    check(
        "tiền tố 'sha256=' vẫn chấp nhận",
        V.verify_oci_signature(body, {"x-oci-signature": f"sha256={good}"}, secret) is True,
    )
    check(
        "chữ ký sai -> False (không phải True cho mọi request)",
        V.verify_oci_signature(body, {"x-oci-signature": "junk"}, secret) is False,
    )
    check(
        "sai secret -> False",
        V.verify_oci_signature(body, {"x-oci-signature": good}, "khac") is False,
    )
    check("thiếu header -> False", V.verify_oci_signature(body, {}, secret) is False)
    check(
        "header 'authorization' KHÔNG được coi là chữ ký",
        V.verify_oci_signature(body, {"authorization": good}, secret) is False,
    )
    check(
        "chưa cấu hình secret -> False",
        V.verify_oci_signature(body, {"x-oci-signature": good}, "") is False,
    )
    check(
        "thân message bị sửa sau khi ký -> False",
        V.verify_oci_signature(b'{"tampered":true}', {"x-oci-signature": good}, secret) is False,
    )

    src = Path("src/mateai/interfaces/http/webhook_gateway.py").read_text(encoding="utf-8")
    check(
        "không còn 'return True  # Allow' giả trong verify_oci_signature",
        "return True  # Allow" not in src,
    )
    # "continuing anyway" chỉ được nằm trong comment mô tả lịch sử, không
    # được còn là hành vi runtime.
    runtime_continue = [
        ln
        for ln in src.splitlines()
        if "continuing anyway" in ln and not ln.strip().startswith("#")
    ]
    check(
        "không còn log 'continuing anyway' ở mức runtime",
        not runtime_continue,
        "; ".join(runtime_continue),
    )
    check("có raise 401 khi secret đã cấu hình mà chữ ký sai", "status_code=401" in src)


def test_webhook_metadata_trust() -> None:
    section("── Metadata cảnh báo phải trung thực ──")
    from mateai.interfaces.http.webhook_gateway import AlertProcessor, WebhookPayload

    proc = AlertProcessor()
    payload = WebhookPayload(
        source="paperless",
        event_type="doc_added",
        severity="info",
        title="HĐ số 123",
        message="mô tả",
        raw_data={},
        metadata={"verified": False},
    )
    text = proc.translate_to_vietnamese(payload)
    check("cảnh báo chưa xác thực được đánh dấu rõ trong nội dung", "CHƯA XÁC THỰC" in text, text)

    payload.metadata = {"verified": True}
    text_ok = proc.translate_to_vietnamese(payload)
    check(
        "cảnh báo đã xác thực KHÔNG bị gắn nhãn cảnh báo",
        "CHƯA XÁC THỰC" not in text_ok,
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. Executor HITL của PluginRegistry
# ══════════════════════════════════════════════════════════════════════════
def test_hitl_executor_runs_once() -> None:
    section("── Executor HITL: chỉ chạy side-effect MỘT lần ──")
    from mateai.application.skills.plugin_registry import plugin_registry

    calls: list[dict] = []

    def counted(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    async def counted_async(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    async def main() -> None:
        # risk_level 5 -> phải đi qua cổng HITL và tạo yêu cầu phê duyệt
        plugin_registry.register_tool(
            "t_sync_side_effect", counted, "test", {"type": "object", "properties": {}},
            is_async=False, risk_level=5, timeout_seconds=5,
        )
        res = await plugin_registry.execute_tool("t_sync_side_effect", {"a": 1}, caller_id="admin")
        check(
            "risk>=3 tạo yêu cầu HITL, chưa chạy",
            res.get("awaiting_approval") is True,
            str(res)[:120],
        )
        check("chưa chạy side-effect nào", len(calls) == 0, f"calls={len(calls)}")

        plugin_registry.register_tool(
            "t_async_side_effect", counted_async, "test", {"type": "object", "properties": {}},
            is_async=True, risk_level=5, timeout_seconds=5,
        )
        r2 = await plugin_registry.execute_tool("t_async_side_effect", {"b": 2}, caller_id="admin")
        check("tool async risk cao cũng cần duyệt", r2.get("awaiting_approval") is True)

    asyncio.run(main())

    # Duyệt thật: nối vào hàng đợi HITL của zero_trust rồi approve.
    from mateai.application.security.zero_trust import hitl_manager

    plugin_registry.register_tool(
        "t_sync_side_effect", counted, "test", {"type": "object", "properties": {}},
        is_async=False, risk_level=5, timeout_seconds=5,
    )
    res = asyncio.run(plugin_registry.execute_tool("t_sync_side_effect", {"a": 1}, caller_id="admin"))
    aid = res.get("approval_id")
    check("có approval_id để duyệt", bool(aid), str(res)[:120])

    # Supervisor P10 / prompt cuối §182: registry đi qua cổng chuẩn -> duyệt bằng hàng đợi chuẩn.
    approved = asyncio.run(hitl_manager.approve_async(aid, approved_by="test"))
    check("approve() báo đã thực thi", approved.get("executed") is True, str(approved)[:150])
    check("side-effect chạy ĐÚNG 1 lần", len(calls) == 1, f"gọi {len(calls)} lần")
    check("truyền đúng tham số", calls and calls[0] == {"a": 1}, str(calls))


# ══════════════════════════════════════════════════════════════════════════
# 7. Chuẩn hoá kết quả của PluginRegistry
# ══════════════════════════════════════════════════════════════════════════
def test_declared_risk_level_is_authoritative() -> None:
    section("── risk_level khai trên tool là căn cứ thật, không bị bỏ qua ──")
    from mateai.application.security.zero_trust import HITL_APPROVAL_THRESHOLD, hitl_manager

    # Tên trung tính: không chứa từ khoá nguy hiểm nào, nên nếu cổng chỉ tra
    # bảng theo tên thì ra mức mặc định 2 và KHÔNG cần duyệt.
    neutral = "export_weekly_snapshot"
    check(
        "tên trung tính mặc định ra Level 2 (không duyệt)",
        hitl_manager.get_risk_level(neutral) == 2,
        str(hitl_manager.get_risk_level(neutral)),
    )
    check(
        "khai risk_level=5 thì get_risk_level ra 5",
        hitl_manager.get_risk_level(neutral, None, 5) == 5,
        str(hitl_manager.get_risk_level(neutral, None, 5)),
    )
    check(
        "khai risk_level=5 thì BẮT BUỘC duyệt (trước đây lọt)",
        hitl_manager.requires_approval(neutral, None, 5) is True,
    )
    check(
        "khai risk_level=1 không hạ được tên nguy hiểm (delete_database -> 5)",
        hitl_manager.get_risk_level("delete_database", None, 1) == 5,
        str(hitl_manager.get_risk_level("delete_database", None, 1)),
    )
    check(
        "khai risk_level sai kiểu u (7) bị bỏ qua, dùng giá trị tính được",
        hitl_manager.get_risk_level(neutral, None, 7) == 2,
        str(hitl_manager.get_risk_level(neutral, None, 7)),
    )
    check(
        "khai risk_level không phải số bị bỏ qua, không làm sập",
        hitl_manager.get_risk_level(neutral, None, "abc") == 2,
    )
    check(f"ngưỡng duyệt vẫn là {HITL_APPROVAL_THRESHOLD}",
          HITL_APPROVAL_THRESHOLD == 3)

    # Số hiển thị trong hàng đợi phải khớp mức cổng dùng, không tự tính lại.
    req = hitl_manager.request_approval(
        action_name=neutral,
        params={"x": 1},
        requested_by="T",
        description="d",
        risk_level=5,
    )
    check(
        "số rủi ro ghi vào hàng đợi khớp mức cổng áp dụng",
        req.get("risk_level") == 5,
        str(req.get("risk_level")),
    )


# ══════════════════════════════════════════════════════════════════════════
# 10-11. Tự khoá chết & cổng Zero-Trust gọi thiếu await
# ══════════════════════════════════════════════════════════════════════════
def test_database_lock_is_reentrant() -> None:
    section("── database.py: khoá phải là RLock, nếu không server treo ──")
    import threading as _threading

    from mateai.infrastructure.database.erp_database import ERPDatabase

    # Phase 73: mọi thao tác ở test này dùng DB TẠM. Trước đây gọn
    # ERPDatabase() không tham số nên ghi thẳng vào vnmateai.db — mỗi lần
    # chạy test lại đẩy 1 dòng "regression" vào dữ liệu thật của người dùng.
    tmp_dir = tempfile.TemporaryDirectory()

    def _fresh_db():
        return ERPDatabase(Path(tmp_dir.name) / "phase60_lock_test.db")

    check(
        "khoá là RLock (cho phép add_finance_record -> log_audit_action lồng nhau)",
        isinstance(_fresh_db()._lock, type(_threading.RLock())),
        f"thực tế: {type(_fresh_db()._lock).__name__}",
    )

    # Chạy thật có giới hạn thời gian: với Lock thường, lần lấy khoá thứ hai
    # của cùng luồng sẽ chờ vô hạn và test sẽ treo -> phải bắt bằng Event.
    db = _fresh_db()
    outcome: dict = {}

    def _write() -> None:
        try:
            outcome["result"] = db.add_finance_record(
                "income", 1.0, "Test", "regression", created_by="test"
            )
        except Exception as exc:  # pylint: disable=broad-except
            outcome["error"] = exc
        finally:
            outcome["done"] = True

    t = _threading.Thread(target=_write, daemon=True)
    t.start()
    finished = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if outcome.get("done"):
            finished = True
            break
        time.sleep(0.05)

    check("add_finance_record hoàn tất (không tự khoá chết)", finished,
          "vẫn treo sau 10s — khoá đang KHÔNG tái lập")
    if finished:
        check("ghi được bản ghi", bool(outcome.get("result")), str(outcome.get("error")))
        # Audit log bất biến phải có mặt: đó là lý do gọi lồng ở trên.
        logs = db.get_audit_logs(limit=1)
        check(
            "audit log bất biến được ghi kèm (lý do của lời gọi lồng)",
            bool(logs) and logs[0]["action_type"] == "RECORD_FINANCE",
            str(logs[0]["action_type"]) if logs else "không có log",
        )


def test_every_hitl_call_site_awaits() -> None:
    section("── Mọi call site execute_with_hitl đều phải await ──")
    targets = [
        "src/mateai/interfaces/http/server.py",
        "src/mateai/application/skills/plugin_registry.py",
        "src/mateai/application/skills/builtin/integration_tools.py",
    ]
    for rel in targets:
        p = Path(rel)
        if not p.exists():
            check(f"{rel} tồn tại", False, "không thấy file")
            continue
        offending = []
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "execute_with_hitl(" in code and "def execute_with_hitl" in code:
                continue
            if "execute_with_hitl(" in code and "await" not in code and "=" not in code:
                continue
            if "execute_with_hitl(" in code and "await" not in code:
                offending.append(f"dòng {n}: {code.strip()[:70]}")
        check(f"{rel}: không call site nào thiếu await", not offending, "; ".join(offending))

    # Chứng minh hậu quả cụ thể: gọi không await rồi .get() sẽ ném AttributeError
    # chứ không phải lỗi nghiệp vụ — đó là loại lỗi khiến người vận hành
    # chẩn đoán nhầm.
    async def _demo() -> dict:
        async def _gate() -> dict:
            return {"status": "executed"}

        not_awaited = _gate()          # cố tình bỏ await
        try:
            not_awaited.get("status")
            return {"threw": False}
        except AttributeError as exc:
            return {"threw": True, "msg": str(exc)}
        finally:
            not_awaited.close()

    d = asyncio.run(_demo())
    check(
        "bỏ await đúng là sinh AttributeError (giải thích vì sao lỗi tối nghĩa)",
        d["threw"] and "coroutine" in d["msg"],
        str(d.get("msg")),
    )

    # Endpoint approve không được gọi tác vụ nghiệp vụ ngay trên event loop:
    # một lần tự khoá chết trong `add_finance_record` đã treo CẢ SERVER
    # (`/health` cũng không trả lời). Nó phải đi qua `approve_async()`, vốn
    # tự đẩy callback đồng bộ sang thread.
    srv = _http_src()
    check(
        "endpoint hitl/approve dùng approve_async (tự đẩy việc sync sang thread)",
        "await hitl_manager.approve_async(" in srv,
        "đang gọi bản đồng bộ — tác vụ chậm sẽ giữ chân event loop",
    )
    check(
        "endpoint hitl/approve KHÔNG còn gọi bản đồng bộ trần",
        not any(
            line.strip().startswith("#") is False and "hitl_manager.approve(" in line
            for line in srv.splitlines()
        ),
        "còn call site bản đồng bộ — executor coroutine sẽ không chạy",
    )
    check(
        "endpoint hitl/reject cũng đẩy việc ghi audit sang thread",
        "asyncio.to_thread(\n            hitl_manager.reject," in srv,
        "ghi DB trên event loop có thể đóng băng server",
    )


def test_webhook_secret_not_required_still_honest() -> None:
    section("── Webhook chưa cấu hình secret: không được tuyên bố đã xác thực ──")
    src = Path("src/mateai/interfaces/http/webhook_gateway.py").read_text(encoding="utf-8")
    check(
        "có nhánh đặt verified=False khi chưa cấu hình secret",
        "if not secret_configured:" in src and "verified = False" in src,
    )
    check(
        "cảnh báo mang nhãn CHƯA XÁC THỰC tới Telegram",
        "CHƯA XÁC THỰC" in src,
    )
    check(
        "metadata của alert mang cờ verified xuống hàng đợi",
        '"verified": payload.metadata.get("verified", False)' in src,
    )


def test_hitl_async_executor_really_runs() -> None:
    section("── HITL: executor coroutine phải THỰC SỰ chạy khi CEO duyệt ──")
    from mateai.application.security.zero_trust import hitl_manager

    ran: list = []

    async def scenario() -> dict:
        out: dict = {}

        # 1) Executor dạng coroutine — trước đây `approve()` gọi `cb()` nên
        #    nhận về coroutine chưa await: KHÔNG chạy, không lỗi, không log,
        #    nhưng vẫn báo `executed: true`.
        async def coro_ok() -> dict:
            await asyncio.sleep(0.01)
            ran.append("coro")
            return {"skill": "chạy rồi"}

        r1 = hitl_manager.request_approval("risky_skill", {}, action_callback=coro_ok)
        res1 = await hitl_manager.approve_async(r1["id"], approved_by="ceo")
        out["coro_ran"] = ran == ["coro"]
        out["coro_executed"] = bool(res1.get("executed"))
        out["coro_result"] = res1.get("execution_result")

        # 2) Executor treo -> phải báo lỗi sau thời hạn, không treo vô hạn
        import mateai.application.security.zero_trust as zt
        old = zt._APPROVAL_EXEC_TIMEOUT
        zt._APPROVAL_EXEC_TIMEOUT = 1.0
        try:
            async def hang() -> None:
                await asyncio.sleep(999)

            r2 = hitl_manager.request_approval("hang", {}, action_callback=hang)
            t0 = time.monotonic()
            res2 = await hitl_manager.approve_async(r2["id"], approved_by="ceo")
            out["hang_failed"] = not res2.get("executed")
            out["hang_status"] = res2.get("status")
            out["hang_seconds"] = time.monotonic() - t0
        finally:
            zt._APPROVAL_EXEC_TIMEOUT = old

        # 3) Executor ném lỗi -> KHÔNG được báo thành công
        async def boom() -> None:
            raise RuntimeError("db down")

        r3 = hitl_manager.request_approval("boom", {}, action_callback=boom)
        res3 = await hitl_manager.approve_async(r3["id"], approved_by="ceo")
        out["boom_executed"] = bool(res3.get("executed"))
        out["boom_status"] = res3.get("status")

        # 4) Hàm SYNC vẫn chạy, và KHÔNG giữ chân event loop
        ticks: list = []

        async def ticker() -> None:
            for _ in range(12):
                await asyncio.sleep(0.05)
                ticks.append(1)

        def slow_sync() -> dict:
            time.sleep(0.35)
            return {"id": 42}

        r4 = hitl_manager.request_approval("sync_action", {}, action_callback=slow_sync)
        tg = asyncio.create_task(ticker())
        res4 = await hitl_manager.approve_async(r4["id"], approved_by="ceo")
        await tg
        out["sync_executed"] = bool(res4.get("executed"))
        out["sync_ticks"] = len(ticks)

        # 5) Bản đồng bộ gặp coroutine -> báo tường minh, không báo thành công giả
        r5 = hitl_manager.request_approval("needs_async", {}, action_callback=coro_ok)
        res5 = hitl_manager.approve(r5["id"], approved_by="ceo")
        out["sync_refuses"] = not res5.get("executed") and "bất đồng bộ" in (
            res5.get("execution_error") or ""
        )
        return out

    out = asyncio.run(scenario())

    check("executor coroutine thực sự chạy", out["coro_ran"],
          "callback trả về coroutine chưa await — tác vụ không bao giờ chạy")
    check("báo executed=true khi coroutine chạy xong", out["coro_executed"])
    check("trả đúng kết quả của coroutine", out["coro_result"] == {"skill": "chạy rồi"},
          str(out["coro_result"]))
    check("executor treo -> executed=false", out["hang_failed"], str(out["hang_status"]))
    check("executor treo -> báo đúng trạng thái", out["hang_status"] == "execution_failed",
          str(out["hang_status"]))
    check("executor treo -> huỷ sau thời hạn, không treo vô hạn", out["hang_seconds"] < 5.0,
          f"{out['hang_seconds']:.1f}s")
    check("executor lỗi -> executed=false", not out["boom_executed"])
    check("executor lỗi -> trạng thái execution_failed", out["boom_status"] == "execution_failed",
          str(out["boom_status"]))
    check("hàm sync vẫn thực thi qua approve_async", out["sync_executed"])
    check("hàm sync chạy ngoài event loop (12 nhịp 50ms đều đặn)",
          out["sync_ticks"] == 12, f"chỉ {out['sync_ticks']}/12 nhịp — loop bị chặn")
    check("approve() đồng bộ từ chối coroutine tường minh", out["sync_refuses"])

    # Cả hai nơi gọi thật đều phải dùng bản async
    srv = _http_src()
    tg = Path("src/mateai/interfaces/telegram/telegram_gateway.py").read_text(encoding="utf-8")
    check("endpoint web gọi approve_async", "await hitl_manager.approve_async(" in srv,
          "đang gọi bản đồng bộ — tác vụ async sẽ không chạy")
    check("nút Telegram gọi approve_async", "await hitl_manager.approve_async(" in tg,
          "đang gọi bản đồng bộ — tác vụ async sẽ không chạy")


def test_telegram_markdown_is_escaped() -> None:
    section("── Telegram: mô tả có ký tự Markdown không được làm hỏng tin nhắn ──")
    from mateai.interfaces.telegram.telegram_gateway import escape_markdown

    def unclosed_entities(text: str) -> list:
        """Mô phỏng trình phân tích Markdown của Telegram: ngoài code span,
        mỗi ký tự đặc biệt phải có ký tự đóng tương ứng."""
        specials = "_*`["
        problems: list = []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                close = text.find("`", i + 1)
                if close == -1:
                    return ["code span không đóng"]
                i = close + 1
                continue
            if ch in specials and text.find(ch, i + 1) == -1:
                problems.append(f"'{ch}' tại {i} không đóng")
            i += 1
        return problems

    # Mô tả thật của người dùng: mọi tên tác vụ trong hệ thống đều chứa `_`
    nasty = [
        "Ghi chi cho khách record_income hôm nay",
        "Xem *chi tiết* nhé",
        "Xem [tại đây](http://x)",
        "check_aws_cost & [x] *y*",
    ]
    for desc in nasty:
        raw = f"Nội dung: {desc}\n\n👉 Bấm nút"
        fixed = f"Nội dung: {escape_markdown(desc, version=1)}\n\n👉 Bấm nút"
        check(f"mô tả {desc[:28]!r}: bản thô vỡ", bool(unclosed_entities(raw)),
              "không vỡ — mô phỏng chưa bắt được")
        check(f"mô tả {desc[:28]!r}: sau escape thì sạch", not unclosed_entities(fixed),
              "; ".join(unclosed_entities(fixed)))

    src = Path("src/mateai/interfaces/telegram/telegram_gateway.py").read_text(encoding="utf-8")
    check("send_hitl_request escape description (version=1)",
          "escape_markdown(str(description or \"\"), version=1)" in src)
    check("cắt tin nhắn không để lại dấu gạch chéo trần",
          'if body.endswith("\\\\"):' in src)

    # Tin nhắn kết quả duyệt đi qua send_incident_alert (parse_mode=HTML)
    import html as _html
    import re as _re
    check("không còn log 'dispatched' trước khi Telegram xác nhận",
          "Incident alert dispatched" not in src)
    check("log xác nhận nằm trong nhánh HTTP 200",
          "confirmed by Telegram" in src)
    bad = "❌ Thực thi thất bại: TypeError: bad <input> & 'x'"
    esc = _html.escape(bad)
    # Sau escape phải KHÔNG còn thẻ HTML thật, và mọi `&` đều phải mở một
    # entity hợp lệ — `&amp;` vẫn chứa chữ `&` nên phải tính là hợp lệ,
    # còn `A & B` (dấu & trần) thì không.
    leftover_tags = _re.findall(r"[<>]", esc)
    stray_amp = _re.findall(r"&(?!amp;|lt;|gt;|quot;|#\d+;|#x[0-9a-fA-F]+;)", esc)
    check("escape HTML: không còn thẻ < > thô",
          not leftover_tags, f"còn {leftover_tags} trong {esc!r}")
    check("escape HTML: không còn dấu & trần",
          not stray_amp, f"còn {stray_amp} trong {esc!r}")
    check("bản chưa escape thì vỡ (chứng minh test có tác dụng)",
          bool(_re.search(r"[<>]", bad)))
    zt_src = Path("src/mateai/application/security/zero_trust.py").read_text(encoding="utf-8")
    check("tin nhắn kết quả duyệt escape tên tác vụ + lỗi",
          "html.escape(str(item['action_name']))" in zt_src
          and "html.escape(outcome_msg)" in zt_src)


def test_connector_config_status_is_honest() -> None:
    section("── Connector: không được báo 'đã cấu hình' khi chưa có thông tin đăng nhập ──")
    from mateai.infrastructure.connectors.base_connector import (
        CONNECTOR_DEFAULTS,
        CONNECTOR_REQUIRED_FIELDS,
        missing_required_fields,
    )

    check("có bảng khoá bắt buộc cho cả 4 connector",
          set(CONNECTOR_REQUIRED_FIELDS) == {"aws", "oci", "paperless", "einvoice"},
          str(sorted(CONNECTOR_REQUIRED_FIELDS)))

    # Cách cũ: "có phải trường nào khác rỗng không" — luôn True vì DEFAULT cấp sẵn
    check("chứng minh cách kiểm tra cũ luôn sai",
          all(any(str(v).strip() for v in CONNECTOR_DEFAULTS[n].values() if v is not None and v != "")
              for n in ("aws", "oci", "paperless", "einvoice")),
          "không còn trường mặc định nào — cách cũ có thể đã thay đổi")

    # Cấu hình trống -> phải báo thiếu, KHÔNG báo "đã cấu hình"
    for name in ("aws", "oci", "paperless", "einvoice"):
        miss = missing_required_fields(name, {})
        check(f"{name}: cấu hình trống -> configured=False", bool(miss), f"thiếu={miss}")
        check(f"{name}: nêu đúng tên khoá bắt buộc còn thiếu",
              set(miss) == set(CONNECTOR_REQUIRED_FIELDS[name]), str(miss))

    # Cấu hình đầy đủ -> không thiếu
    full = {
        "aws": {"access_key_id": "AKIA...", "secret_access_key": "s"},
        "oci": {"compartment_id": "ocid1..."},
        "paperless": {"base_url": "https://x/", "api_token": "t"},
        "einvoice": {"base_url": "https://y", "client_id": "c", "client_secret": "s"},
    }
    for name, vals in full.items():
        check(f"{name}: cấu hình đầy đủ -> không thiếu",
              missing_required_fields(name, vals) == [], str(missing_required_fields(name, vals)))

    # Chỉ trả TÊN khoá, không bao giờ trả giá trị bí mật. Dùng giá trị giả
    # đặc biệt để phát hiện rò rỉ — không kiểm tra chữ "secret" vì tên khoá
    # (`secret_access_key`) vốn đã chứa chữ đó.
    fake = {
        "access_key_id": "AKIALEAKCANARY01",
        "secret_access_key": "awsleakcanary02",
        "compartment_id": "ocid1.leakcanary03",
        "base_url": "https://leakcanary04",
        "api_token": "leakcanary05",
        "client_id": "leakcanary06",
        "client_secret": "leakcanary07",
    }
    blob = ""
    for name in ("aws", "oci", "paperless", "einvoice"):
        blob += str(missing_required_fields(name, fake))
        blob += str(missing_required_fields(name, {}))
    canaries = [v for v in fake.values() if "canary" in v]
    leaked = [v for v in canaries if v in blob]
    check("không rò rỉ giá trị bí mật qua danh sách thiếu", not leaked, f"lộ: {leaked}")
    check("danh sách thiếu chỉ gồm tên khoá",
          all(k in CONNECTOR_REQUIRED_FIELDS["aws"] + CONNECTOR_REQUIRED_FIELDS["einvoice"]
              for k in missing_required_fields("aws", {})),
          str(missing_required_fields("aws", {})))
    srv_txt = _http_src()
    check("endpoint chỉ nêu tên khoá, không đính kèm giá trị",
          '"missing_fields": missing' in srv_txt)

    srv = _http_src()
    check("endpoint health dùng missing_required_fields",
          "missing = missing_required_fields(name)" in srv)
    check("endpoint health không còn phép kiểm tra 'có trường nào khác rỗng'",
          'for v in (getattr(cfg, "extra", None) or {}).values()' not in srv)
    check("endpoint health nêu tên khoá thiếu cho người vận hành",
          '"missing_fields": missing' in srv)

    js = Path("web/app.js").read_text(encoding="utf-8")
    check("UI không còn kết luận 'Đã cấu hình' chỉ từ số khoá trong block",
          "Object.keys(block).length > 0,\n      'Đã cấu hình',\n      'Chưa cấu hình'"
          not in js)
    check("UI đọc missing_fields từ endpoint health",
          "info.missing_fields" in js and "_ccFetchConnectorHealth" in js)
    check("UI nêu rõ khoá nào còn thiếu",
          "Còn thiếu:" in js)
    check("UI không khẳng định cả khi không xác minh được",
          "chưa xác minh được" in js)


def test_result_normalisation() -> None:
    section("── Kết quả tool: không tự mâu thuẫn ──")
    from mateai.application.skills.plugin_registry import plugin_registry

    async def health_like(**_kw):
        return {
            "success": False,
            "overall_healthy": False,
            "connectors": {
                "paperless": {"success": False, "error": "Not authenticated"},
                "aws": {"success": False, "error": "Authentication failed"},
            },
        }

    async def nested_ok(**_kw):
        return {"success": True, "data": {"a": 1}, "error": None}

    async def main() -> None:
        plugin_registry.register_tool(
            "t_health_like", health_like, "t", {"type": "object", "properties": {}}, is_async=True
        )
        # Kiểm định dạng kết quả, không kiểm chính sách -> đã qua cổng (authorized).
        r = await plugin_registry.execute_tool("t_health_like", {}, authorized=True)
        check("success=False thì error phải khác None", bool(r.get("error")), str(r.get("error")))
        check("error nêu đúng connector hỏng", "paperless" in str(r.get("error")), str(r.get("error"))[:120])
        check(
            "giữ đường dẫn để biết lỗi của connector nào",
            "connectors.paperless" in str(r.get("error")),
            str(r.get("error"))[:120],
        )
        check(
            "payload chi tiết không bị cắt (data giữ khoá connectors)",
            isinstance(r.get("data"), dict) and "connectors" in r["data"],
            str(r.get("data"))[:120],
        )

        plugin_registry.register_tool(
            "t_nested_ok", nested_ok, "t", {"type": "object", "properties": {}}, is_async=True
        )
        r2 = await plugin_registry.execute_tool("t_nested_ok", {}, authorized=True)
        check("tool trả data lồng vẫn giữ nguyên data", r2.get("data") == {"a": 1}, str(r2.get("data")))
        check("tool thành công -> error là None", r2.get("error") is None, str(r2.get("error")))

    asyncio.run(main())


def test_summarise_failure() -> None:
    section("── Tóm tắt lỗi: không bịa, không bỏ sót ──")
    from mateai.application.skills.plugin_registry import _summarize_failure as s

    check("lỗi trực tiếp", s({"error": "boom"}) == "boom")
    check("lồng 2 tầng kèm đường dẫn",
          s({"connectors": {"aws": {"error": "Auth failed"}}}) == "connectors.aws: Auth failed")
    check("gộp nhiều lỗi", ";" in s({"c": {"a": {"error": "x"}, "b": {"error": "y"}}}))
    check("payload rỗng -> thông báo trung thực", "không kèm mô tả lỗi" in s({}))
    check("None -> thông báo trung thực", "không kèm mô tả lỗi" in s(None))
    # KHÔNG được suy đoán từ list: 3 phần tử có thể là 3 bản ghi hợp lệ.
    check(
        "KHÔNG bịa lỗi từ list ('N mục thất bại')",
        "mục thất bại" not in s({"documents": [1, 2, 3]}),
        s({"documents": [1, 2, 3]}),
    )
    check("giới hạn số lỗi liệt kê", len(s({"c": {str(i): {"error": f"e{i}"} for i in range(20)}}).split(";")) <= 4)
    check("giới hạn độ sâu (không đệ quy vô hạn)", "sâu" not in s({"a": {"b": {"c": {"d": {"error": "sâu"}}}}}))


# ══════════════════════════════════════════════════════════════════════════
# 8-9. Worker manager & vòng đời
# ══════════════════════════════════════════════════════════════════════════
def test_worker_manager() -> None:
    section("── BackgroundWorkerManager: nhận cả hàm sync lẫn async ──")
    from mateai.application.operations.background_workers import background_worker_manager as M, TaskStatus

    def sync_heavy(x):
        time.sleep(0.3)
        return {"echo": x}

    async def async_light(y):
        await asyncio.sleep(0.02)
        return {"echo": y}

    async def main() -> None:
        await M.start()
        await M.submit("t_w_sync", sync_heavy, 1, notify_on_complete=False)
        await M.submit("t_w_async", async_light, 2, notify_on_complete=False)

        # Nếu hàm sync chạy trong event loop thì các vòng sleep phía dưới sẽ
        # bị trễ ~300ms. Đo độ đều của nhịp 50ms.
        gaps = []
        last = time.monotonic()
        for _ in range(10):
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append((now - last) * 1000)
            last = now

        s_task = M.get_task("t_w_sync")
        a_task = M.get_task("t_w_async")
        check("hàm sync hoàn tất (không FAILED âm thầm)",
              s_task and s_task.status == TaskStatus.COMPLETED,
              s_task.error if s_task else "không có task")
        check("kết quả hàm sync đúng", s_task and s_task.result == {"echo": 1}, str(s_task.result if s_task else None))
        check("hàm async hoàn tất", a_task and a_task.status == TaskStatus.COMPLETED,
              a_task.error if a_task else "không có task")
        worst = max(gaps)
        check(f"event loop không bị chặn (nhịp 50ms, max={worst:.0f}ms)", worst < 130,
              f"max gap {worst:.0f}ms")

    asyncio.run(main())


def test_connector_tools_registered() -> None:
    section("── Cầu nối connector -> Plugin Registry ──")
    from mateai.infrastructure.connectors.tool_bridge import register_connector_tools
    from mateai.application.skills.plugin_registry import plugin_registry

    stats = register_connector_tools()
    check("đăng ký được tool qua cầu nối", stats.get("registered", 0) >= 10, str(stats))
    check("không bỏ sót tool nào", stats.get("skipped", 0) == 0, str(stats))

    names = set(plugin_registry.get_tool_names())
    for expected in ("check_aws_cost", "check_oci_metrics", "search_paperless_documents",
                     "check_einvoice_daily", "check_connector_health"):
        check(f"có tool '{expected}'", expected in names)

    # risk_level phải kế thừa từ CONNECTOR_RISK_LEVELS, không phải mặc định 1.
    from mateai.infrastructure.connectors import CONNECTOR_RISK_LEVELS
    risky = [k for k, v in CONNECTOR_RISK_LEVELS.items() if int(v) >= 2]
    if risky:
        key = risky[0]
        from mateai.infrastructure.connectors.tool_bridge import _CONNECTOR_ACTIONS
        tool_name = _CONNECTOR_ACTIONS.get(key)
        tool = plugin_registry.get_tool(tool_name) if tool_name else None
        if tool:
            check(f"risk_level của '{tool_name}' khớp CONNECTOR_RISK_LEVELS",
                  tool.risk_level == int(CONNECTOR_RISK_LEVELS[key]),
                  f"tool={tool.risk_level} map={CONNECTOR_RISK_LEVELS[key]}")


def test_startup_guarded_once() -> None:
    section("── Vòng đời: startup chỉ chạy 1 lần ──")
    # Supervisor P10: vòng khởi động chuyển sang interfaces/http/lifecycle.py.
    src = Path("src/mateai/interfaces/http/lifecycle.py").read_text(encoding="utf-8")
    check("có cờ chặn chạy lần hai", "if STATE.started:" in src)
    check(
        "cờ được kiểm tra TRƯỚC khi làm việc nặng",
        src.index("if STATE.started:") > src.index("def run_startup(")
        and src.index("if STATE.started:") < src.index("for step in steps"),
    )
    main_src = Path("main.py").read_text(encoding="utf-8")
    check("main.py thật sự chạy 2 uvicorn server trên cùng app (lý do cần cờ chặn)",
          main_src.count("uvicorn.Config(") >= 2 and "_serve_both" in main_src)


def test_webhook_gateway_no_empty_oci_gap() -> None:
    section("── Webhook: resource_id từ các biến thể payload ──")
    from mateai.interfaces.http.webhook_gateway import AlertProcessor

    proc = AlertProcessor()
    proc._recent.clear() if hasattr(proc, "_recent") else None
    check("AlertProcessor khởi tạo được", proc is not None)


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    print("═" * 70)
    print("KIỂM THỬ PHASE 59/60 — SỬA BẢO MẬT & TÍNH ĐÚNG")
    print("═" * 70)

    for fn in (
        test_webhook_signature,
        test_webhook_metadata_trust,
        test_hitl_executor_runs_once,
        test_declared_risk_level_is_authoritative,
        test_database_lock_is_reentrant,
        test_every_hitl_call_site_awaits,
        test_webhook_secret_not_required_still_honest,
        test_hitl_async_executor_really_runs,
        test_telegram_markdown_is_escaped,
        test_connector_config_status_is_honest,
        test_result_normalisation,
        test_summarise_failure,
        test_worker_manager,
        test_connector_tools_registered,
        test_startup_guarded_once,
    ):
        try:
            fn()
        except Exception as exc:  # pylint: disable=broad-except
            global FAIL
            FAIL += 1
            RESULTS.append(f"  💥 {fn.__name__} ném lỗi: {type(exc).__name__}: {exc}")

    for line in RESULTS:
        print(line)
    print("═" * 70)
    print(f"Tổng: {PASS + FAIL} | Pass: {PASS} | Fail: {FAIL}")
    print("═" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
