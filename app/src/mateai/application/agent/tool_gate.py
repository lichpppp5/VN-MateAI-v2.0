# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/agent/tool_gate.py
=====================================
Cổng DUY NHẤT thực thi tool do LLM yêu cầu: `run_tool_with_policy`.

Tác vụ cần duyệt nằm trong hàng đợi HITL duy nhất (`zero_trust.hitl_manager`,
`kind="tool"`); `execute_approved_tool` là executor chạy lại tool qua cổng này
với `approved=True` sau khi người có quyền duyệt — ở bất kỳ kênh nào.

Dùng bởi vòng agent `LLMEngine.ask_async` — vòng mà mọi kênh voice đi qua khi
câu hỏi cần tool (core/voice_turn.py). Phase 3 đã gỡ vòng tool 1-bước riêng của
portal (execute_tool_call, can_synthesize_direct_response, prune_tool_schemas,
prune_tool_payload_for_llm): chọn tool là việc của core/dynamic_skill_router.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Set

from mateai.application.security.safety_guard import security_engine
from mateai.application.security.zero_trust import hitl_manager

logger = logging.getLogger(__name__)

#: Loại yêu cầu HITL do cổng tool tạo — chạy lại bằng `execute_approved_tool`.
TOOL_KIND = "tool"


_LOCAL_TARGETS = ("master", "local", "server", "chính", "cục bộ")

#: Tool của Plugin Registry (không qua `@export_skill`) cần màn hình khi chạy trên máy chủ.
_DESKTOP_REGISTRY_TOOLS = frozenset({"tool_execute_gui_task"})


def _desktop_problem(fn_name: str) -> Optional[Dict[str, Any]]:
    """Tool cần màn hình (`needs_desktop`) mà máy chủ chạy ở phiên nền -> kết quả lỗi rõ ràng; None = chạy được.

    Không chặn thì tool vẫn báo "thành công" nhưng không ai thấy cửa sổ, và AI tự tạo kỹ năng mới liên tục (sự cố 2026-10-08).
    """
    try:
        from core.plugin_manager import plugin_manager
        needs = fn_name in _DESKTOP_REGISTRY_TOOLS or bool((plugin_manager.get_tool_contract(fn_name) or {}).get("needs_desktop"))
        if not needs:
            return None
        from mateai.infrastructure.desktop_session import status
        st = status()
    except Exception:  # noqa: BLE001 — không kiểm được thì không chặn
        return None
    if st.get("interactive"):
        return None
    msg = (f"Không chạy '{fn_name}' trên máy chủ: {st.get('reason')}. Đây KHÔNG phải do thiếu kỹ năng — đừng tạo kỹ năng mới. "
           "Hãy nói với người dùng: chạy VN-MateAI trong phiên Windows đang đăng nhập (Start.bat) để mở được cửa sổ trên máy chủ, "
           "hoặc chọn một máy trạm đang online (list_online_workstations rồi gọi lại với target_client).")
    return {"status": "error", "success": False, "code": "DESKTOP_UNAVAILABLE", "error": msg, "message": msg,
            "desktop": {k: st.get(k) for k in ("session_id", "console_session_id", "reason")}}


def _topo(kind: str, **kw: Any) -> None:
    try:
        from mateai.application.operations.topology_events import publish
        publish(kind, **kw)
    except Exception:  # noqa: BLE001 — giám sát không được làm hỏng tác vụ
        pass


async def run_tool_with_policy(fn_name: str, fn_args: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    """Cổng tool (xem `_run_tool_with_policy`) + báo từng bước cho trang giám sát
    `/admin/topology`: bắt đầu, xong / lỗi / bị chặn / chờ duyệt, thời gian chạy."""
    target = str((fn_args or {}).get("target_client_id") or (fn_args or {}).get("target_client") or "master")
    # Chạy trên máy chủ -> ô "core" (trước: "tools" -> "tools", cạnh tự nối vào chính nó).
    where = "core" if target.strip().lower() in _LOCAL_TARGETS else f"worker:{target}"
    _topo("tool", stage="start", source="tools", target=where, status="running",
          detail=f"{fn_name} @ {target}")
    t0 = time.perf_counter()
    from mateai.infrastructure.observability.tracing import set_attrs, span
    try:
        with span("tool.execute", **{"tool.name": fn_name, "tool.target": target,
                                     "tool.caller": kw.get("caller")}) as sp:
            gate = await _run_tool_with_policy(fn_name, fn_args, **kw)
            _r = gate.get("result") if isinstance(gate, dict) else None
            set_attrs(sp, **{"tool.status": (_r or {}).get("status") if isinstance(_r, dict) else None})
    except Exception as exc:
        _topo("tool", stage="end", source="tools", target=where, status="error",
              ms=(time.perf_counter() - t0) * 1000, detail=f"{fn_name}: {type(exc).__name__}")
        raise
    ms = (time.perf_counter() - t0) * 1000
    res = gate.get("result") if isinstance(gate, dict) else None
    res = res if isinstance(res, dict) else {}
    st = res.get("status")
    if st in ("need_confirm", "awaiting_approval"):
        _topo("approval", stage="request", source="tools", target="hitl", status="waiting", ms=ms,
              detail=f"{fn_name} chờ duyệt (người yêu cầu: {kw.get('caller') or '?'})")
    elif st == "success" or res.get("success") is True:
        _topo("tool", stage="end", source=where, target="tools", status="ok", ms=ms, detail=fn_name)
    else:
        why = str(res.get("code") or res.get("error") or res.get("message") or st or "lỗi")
        _topo("tool", stage="end", source=where, target="tools", status="error", ms=ms,
              detail=f"{fn_name}: {why}")
    return gate


async def _run_tool_with_policy(
    fn_name: str,
    fn_args: Dict[str, Any],
    *,
    caller: str,
    source_device: Optional[str],
    query: str = "",
    session_id: Optional[str] = None,
    registry_names: Optional[Set[str]] = None,
    approved: bool = False,
    client_timeout: Optional[float] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cổng DUY NHẤT thực thi một tool do LLM yêu cầu (mọi kênh voice + agent).

    Quyết định: `policy_engine.authorize()` (kill switch -> L5/từ khoá cấm -> RBAC
    -> rủi ro -> duyệt / uỷ quyền). DENY luôn thắng, kể cả admin, kể cả `approved`.
    Rủi ro >= 3 cần duyệt cho MỌI vai trò, trừ khi có uỷ quyền còn hạn cho đúng
    danh tính + tool (L4). Thay quy tắc cũ "admin bỏ qua duyệt" và f389bbe
    (prompt Supervisor thay thế hoàn toàn, 2026-10-05).

    `caller`: danh tính người/thiết bị cho RBAC + audit. `source_device`: kênh gọi
    — dùng để gửi yêu cầu duyệt về đúng nơi và suy ra tác nhân (`agent_id`).

    Trước Phase 3 logic này nằm trong `LLMEngine.ask_async`; đường voice
    realtime dùng một bản riêng import `zero_trust.evaluate_risk` (không tồn
    tại) nên mọi tool chạy KHÔNG qua kiểm tra nào.

    `approved`: True CHỈ khi người có quyền đã duyệt tác vụ này (đường resume
    sau "Đồng ý"). Cờ `confirmed` trong `fn_args` bị bỏ đi và không có hiệu
    lực: tham số tool do LLM hoặc client gửi lên, nên ai cũng tự đặt được —
    trước đây `{"confirmed": true}` trong tham số là đủ để bỏ qua HITL.

    `client_timeout`: thời gian chờ máy trạm (giây) khi tool chạy trên máy trạm;
    bỏ trống = mặc định của orchestrator.

    Trả về {"target_client", "args", "result"}.
    """
    from mateai.application.security import policy_engine
    from core.plugin_manager import plugin_manager

    fn_args = dict(fn_args or {})
    fn_args.pop("confirmed", None)
    target_client = str(
        fn_args.pop("target_client_id", None)
        or fn_args.pop("target_client", "master")
        or "master"
    ).strip()
    agent = agent_id or policy_engine.agent_id_for(source_device)
    # ABAC: skill đọc dữ liệu theo phòng ban / cấp bảo mật của NGƯỜI được phục vụ (danh tính
    # máy chủ xác thực, không phải tham số LLM). Ngữ cảnh con của lời gọi này — không lan.
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    CURRENT_PRINCIPAL.set(caller)

    def _done(result: Dict[str, Any]) -> Dict[str, Any]:
        return {"target_client": target_client, "args": fn_args, "result": result}

    declared = None
    try:  # rủi ro do nơi đăng ký tool khai (chỉ nâng, không hạ)
        from mateai.application.skills.plugin_registry import plugin_registry as _reg
        _def = _reg.get_tool(fn_name)
        declared = _def.risk_level if _def is not None else None
    except Exception:  # noqa: BLE001
        declared = None
    decision = policy_engine.authorize(fn_name, fn_args, caller=caller, agent_id=agent,
                                       approved=approved, declared_risk=declared, session_id=session_id)
    from opentelemetry import trace as _otel
    from mateai.infrastructure.observability.tracing import set_attrs as _set_attrs
    _set_attrs(_otel.get_current_span(), **{"policy.decision": decision.effect, "policy.rule": decision.rule,
                                            "policy.level": decision.level, "policy.risk": decision.risk,
                                            "policy.version": decision.policy_version, "agent.id": agent})
    audit_ctx = {"agent_id": agent, "target": target_client, "session_id": session_id,
                 "decision": decision.effect, "level": decision.level, "rule": decision.rule,
                 "policy_version": decision.policy_version, "args": fn_args}

    from mateai.application.tasks import ledger
    from mateai.application.tasks.verification import verify
    op_task = ledger.CURRENT_TASK.get()
    audit_ctx["op_task_id"] = op_task

    if decision.effect == policy_engine.DENY:
        logger.warning("[Policy] DENY tool=%s caller=%s agent=%s rule=%s", fn_name, caller, agent, decision.rule)
        security_engine.log_audit(str(caller), fn_name, str(decision.risk), "REJECTED",
                                  {**audit_ctx, "reason": decision.reasons[0]})
        ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                           result={"status": "denied", "error": decision.reasons[0]},
                           verification={"level": "NONE", "status": "failed",
                                         "checks": [f"chính sách từ chối ({decision.rule}): {decision.reasons[0]}"]})
        return _done({
            "status": "error", "success": False,
            "code": "RBAC_DENIED" if decision.rule == "rbac" else "POLICY_DENIED",
            "rule": decision.rule, "error": decision.reasons[0], "message": decision.reasons[0],
        })

    if decision.effect == policy_engine.REQUIRE_APPROVAL:
        logger.warning("Zero-Trust Security: Tác vụ '%s' yêu cầu phê duyệt.", fn_name)
        _chat_id = None
        if source_device and "telegram:" in str(source_device):
            _parts = str(source_device).split(":")
            if len(_parts) >= 2:
                _chat_id = _parts[1]
        # Yêu cầu tự ghi audit (HITL_APPROVAL_REQUEST_*, status pending) và báo
        # Telegram cho người duyệt — không ghi thêm dòng PENDING riêng ở đây.
        # Giải thích cho người duyệt: AI muốn làm gì, theo yêu cầu nào của ai, vì sao cần duyệt (security/explain.py).
        from mateai.application.security.explain import explain_approval
        _explain = explain_approval(fn_name, fn_args, decision.risk, decision.reasons, query=query,
                                    requested_by=str(caller), target=target_client)
        _req = hitl_manager.request_approval(
            action_name=fn_name,
            params=dict(fn_args),
            requested_by=caller,
            description=_explain["text"],
            kind=TOOL_KIND,
            context={
                "explanation": _explain,
                "target_client": target_client,
                "query": query,
                "chat_id": _chat_id,
                "source_device": source_device,
                "session_id": session_id,
                "agent_id": agent,
                "op_task_id": op_task,
            },
            risk_level=decision.risk,
        )
        _pending = {"status": "need_confirm", "approval_id": _req.get("id")}
        ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                           result=_pending, verification=verify(fn_name, fn_args, _pending, decision.risk, target_client))
        return _done({
            "status": "need_confirm",
            "approval_id": _req.get("id"),
            "message": f"Tác vụ '{fn_name}' yêu cầu phê duyệt. Nhắn 'Đồng ý' để em chạy tiếp.",
            "skill": fn_name, "target_client": target_client,
            "args": fn_args, "requires_confirmation": True,
        })

    if decision.rule == "delegated":
        security_engine.log_audit(str(caller), fn_name, str(decision.risk), "APPROVAL_REMEMBERED", audit_ctx)

    if target_client.lower() in _LOCAL_TARGETS:
        _desk = _desktop_problem(fn_name)
        if _desk:
            logger.warning("[Desktop] Từ chối %s: %s", fn_name, _desk["desktop"].get("reason"))
            ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision, result=_desk,
                               verification={"level": "NONE", "status": "failed", "checks": [_desk["error"]]})
            security_engine.log_audit(str(caller), fn_name, str(decision.risk), "FAILED", {**audit_ctx, "reason": "DESKTOP_UNAVAILABLE"})
            return _done(_desk)

    if target_client.lower() in _LOCAL_TARGETS:
        _plugin_registry = None
        if registry_names is None:
            try:
                from mateai.application.skills.plugin_registry import plugin_registry as _plugin_registry
                registry_names = set(_plugin_registry.get_tool_names())
            except Exception:
                registry_names = set()
        if registry_names and fn_name in registry_names:
            if _plugin_registry is None:
                from mateai.application.skills.plugin_registry import plugin_registry as _plugin_registry
            logger.info("[Phase60] Thực thi tool Plugin Registry: '%s'", fn_name)
            _result = await _plugin_registry.execute_tool(fn_name, fn_args, caller_id=caller, authorized=True)
            if _result.get("awaiting_approval"):
                _result = {
                    "status": "awaiting_approval", "success": False,
                    "approval_id": _result.get("approval_id"),
                    "risk_level": _result.get("risk_level"),
                    "message": "Tác vụ này đang chờ phê duyệt. CHƯA được thực thi.",
                }
        else:
            # Chỉ ghi TÊN tham số: giá trị có thể là nội dung tệp sắp ghi, câu lệnh,
            # dữ liệu nhân sự… và `/api/v1/logs/recent` phục vụ log cho cả viewer.
            # Giá trị đầy đủ nằm trong audit_logs (chỉ admin đọc).
            logger.info("Thực thi kỹ năng cục bộ: '%s' tham số=%s", fn_name, sorted(fn_args))
            _result = await plugin_manager.execute_skill(fn_name, fn_args)
    else:
        logger.info("Diều phối kỹ năng '%s' → [%s]", fn_name, target_client)
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        _sync_kw = {"timeout": client_timeout} if client_timeout else {}
        _result = await asyncio.to_thread(
            orchestrator.execute_on_client_sync, target_client, fn_name, fn_args, **_sync_kw)

    _ok = _result.get("status") == "success" or _result.get("success") is True
    # Kiểm chứng sau hành động (§29): "tool báo đã chạy" chưa phải "đã xong".
    _verification = verify(fn_name, fn_args, _result, decision.risk, target_client)
    if isinstance(_result, dict):
        _result = {**_result, "verification": {k: _verification[k] for k in ("level", "status", "summary")}}
    # Dữ liệu cá nhân (SĐT, CCCD, lương, STK…) che TRƯỚC khi AI đọc, theo quyền của người được phục vụ — AI không thể lộ
    # thứ nó chưa thấy, ở mọi kênh (pii_guard). Sổ tác vụ LUÔN lưu bản đã che: manager đọc được sổ (/api/v1/ops/tasks).
    from mateai.application.security.pii_guard import guard_result, mask_obj
    ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                       result=mask_obj(_result)[0], verification=_verification)
    _result, _pii = guard_result(_result, caller)
    if _pii:
        audit_ctx["pii_masked"] = _pii
    security_engine.log_audit(str(caller), fn_name, str(decision.risk), "SUCCESS" if _ok else "FAILED",
                              {**audit_ctx, "verification": _verification["status"]})
    return _done(_result)


async def execute_approved_tool(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executor của hàng đợi HITL cho `kind="tool"`: chạy lại đúng tool + tham số
    + máy đích đã lưu, qua cổng này với `approved=True`. RBAC áp theo người
    YÊU CẦU (không phải người duyệt) — duyệt không mở rộng quyền của người hỏi.
    """
    ctx = item.get("context") or {}
    _remember_approval(item)
    from mateai.application.tasks import ledger
    _token = ledger.CURRENT_TASK.set(ctx.get("op_task_id"))
    _topo("approval", stage="approved", source="hitl", target="tools", status="ok",
          detail=f"{item.get('action_name')} được duyệt bởi {item.get('reviewed_by') or '?'}")
    gate = await run_tool_with_policy(
        str(item.get("action_name") or ""),
        {**dict(item.get("params") or {}), "target_client": ctx.get("target_client") or "master"},
        caller=str(item.get("requested_by") or "anonymous"),
        source_device=ctx.get("source_device"),
        query=str(ctx.get("query") or ""),
        session_id=ctx.get("session_id"),
        approved=True,
        agent_id=ctx.get("agent_id"),
    )
    ledger.settle(ctx.get("op_task_id"))
    ledger.CURRENT_TASK.reset(_token)
    return gate["result"]


def pending_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """Dạng hiển thị của một yêu cầu đang chờ (portal, HUD, hội thoại)."""
    ctx = item.get("context") or {}
    return {
        "id": item.get("id"),
        "tool_name": item.get("action_name"),
        "arguments": item.get("params") or {},
        "target_client": ctx.get("target_client") or "master",
        "query": ctx.get("query") or "",
        "description": item.get("description") or "",
        "requested_by": item.get("requested_by"),
        "risk_level": item.get("risk_level"),
        "kind": item.get("kind"),
        "chat_id": ctx.get("chat_id"),
        "source_device": ctx.get("source_device"),
        "created_at": item.get("created_at"),
    }


def _remember_approval(item: Dict[str, Any]) -> None:
    """Người duyệt vừa đồng ý tác vụ do robot / kênh Telegram yêu cầu -> uỷ quyền
    có hạn (L4) cho đúng danh tính + tool (`policy_engine.remember_delegation`)."""
    from mateai.application.security.policy_engine import remember_delegation
    remember_delegation(item.get("requested_by"), str(item.get("action_name") or ""),
                        str(item.get("reviewed_by") or ""))


hitl_manager.register_executor(TOOL_KIND, execute_approved_tool)
