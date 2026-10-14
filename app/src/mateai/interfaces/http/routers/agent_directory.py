# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/agent_directory.py
=================================================
Danh bạ Agent (docs/integrations/agent-gateway.md).

- `/api/v1/agent-gateway/agents/...` — quản trị (xem: manager + admin; ghi: admin).
- `/api/v1/agents/inbound/...` — agent NGOÀI gọi vào bằng token riêng `Authorization: Bearer <agent_id>.<bí mật>` (KHÔNG nhận JWT người
  dùng; middleware và route đều kiểm). Danh tính `agent:<id>`; quyền, duyệt, audit, ngân sách như mọi danh tính khác.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from mateai.application.agent_gateway import directory as d
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()
_READ = Depends(require_roles(["manager", "admin"]))
_WRITE = Depends(require_roles(["admin"]))
_TAG = ["Agent Directory"]
_BASE = "/api/v1/agent-gateway/agents"
INBOUND_PREFIX = "/api/v1/agents/inbound/"


def _who(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "admin")


def _422(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ── quản trị ────────────────────────────────────────────────────────────────

@router.get(_BASE, summary="Mọi agent nối với VN-MateAI (inbound, A2A, MCP) + tình trạng", tags=_TAG)
async def list_agents(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"agents": d.list_all()}


@router.get(_BASE + "/{agent_id}", summary="Chi tiết một agent", tags=_TAG)
async def get_agent(agent_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    a = d.get(agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Không có agent này")
    return a


@router.put(_BASE + "/{agent_id}", summary="Tạo / sửa agent inbound hoặc A2A", tags=_TAG)
async def save_agent(agent_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        saved = d.save(agent_id, body, _who(user))
    except d.DirectoryError as exc:
        raise _422(exc)
    if saved["kind"] == "a2a":
        saved = (await d.refresh_card(agent_id, _who(user)))["agent"]
    return saved


@router.delete(_BASE + "/{agent_id}", summary="Xoá agent (gỡ công cụ, token hết hiệu lực)", tags=_TAG)
async def delete_agent(agent_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    if not d.delete(agent_id, _who(user)):
        raise HTTPException(status_code=404, detail="Không có agent này")
    return {"status": "success"}


@router.post(_BASE + "/{agent_id}/status", summary="Kill switch: active | suspended (MCP: bật / tắt server)", tags=_TAG)
async def set_status(agent_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return d.set_status(agent_id, str(body.get("status") or ""), _who(user))
    except d.DirectoryError as exc:
        raise HTTPException(status_code=404 if "Không có" in str(exc) else 422, detail=str(exc))


@router.post(_BASE + "/{agent_id}/token", summary="Cấp / xoay token cho agent inbound (chỉ hiện MỘT lần)", tags=_TAG)
async def issue_token(agent_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return {"agent_id": agent_id, "token": d.issue_token(agent_id, _who(user)),
                "usage": f"Authorization: Bearer <token> → {INBOUND_PREFIX}ask"}
    except d.DirectoryError as exc:
        raise _422(exc)


@router.post(_BASE + "/{agent_id}/card", summary="Đọc lại agent card (A2A) và đăng ký lại công cụ", tags=_TAG)
async def refresh_card(agent_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return await d.refresh_card(agent_id, _who(user))
    except d.DirectoryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── inbound: agent ngoài gọi vào ────────────────────────────────────────────

def _bearer(authorization: Optional[str]) -> str:
    m = re.match(r"^Bearer\s+(.+)$", str(authorization or "").strip())
    return m.group(1).strip() if m else ""


def inbound_agent(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    row, why = d.authenticate(_bearer(authorization))
    if not row:
        raise HTTPException(status_code=401 if "không hợp lệ" in why else 403, detail=why, headers={"WWW-Authenticate": "Bearer"})
    return row


class AskBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="Việc cần Ly Ly làm, viết bằng lời")
    conversation_id: str = Field("default", pattern=r"^[A-Za-z0-9._-]{1,64}$", description="Giữ ngữ cảnh giữa các lượt")


class ToolBody(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)


def _principal(row: Dict[str, Any]) -> str:
    from mateai.application.security.security_guard import AGENT_PRINCIPAL_PREFIX
    return AGENT_PRINCIPAL_PREFIX + row["agent_id"]


@router.post(INBOUND_PREFIX + "ask", summary="Agent ngoài giao một việc bằng lời (vòng agent đầy đủ, theo quyền của agent)", tags=_TAG)
async def inbound_ask(body: AskBody, agent: Dict[str, Any] = Depends(inbound_agent)) -> Dict[str, Any]:
    from mateai.application.agent.llm_engine import llm_engine
    who = _principal(agent)
    out = await llm_engine.ask_async(body.message, source_device=who, caller=who, session_id=f"{who}:{body.conversation_id}")
    return {"reply": out.get("reply"), "success": out.get("success", True), "error": out.get("error"),
            "requires_confirmation": bool(out.get("requires_confirmation")), "approval_id": out.get("approval_id"),
            "tools_used": [tc.get("skill") for tc in out.get("tool_calls_made") or [] if tc.get("skill")]}


@router.get(INBOUND_PREFIX + "tools", summary="Công cụ agent này được phép gọi (theo quyền)", tags=_TAG)
async def inbound_tools(agent: Dict[str, Any] = Depends(inbound_agent)) -> Dict[str, Any]:
    from core.plugin_manager import plugin_manager
    from mateai.application.security.risk_engine import assess_risk
    from mateai.application.security.security_guard import security_guard
    who = _principal(agent)
    role = security_guard._resolve_role(who)
    tools: List[Dict[str, Any]] = []
    for t in plugin_manager.get_all_tools():
        fn = t.get("function") or {}
        name = fn.get("name")
        if name and security_guard._evaluate_rbac(name, role, who)[0]:
            risk = assess_risk(name)
            tools.append({"name": name, "description": fn.get("description"), "parameters": fn.get("parameters"),
                          "risk": risk, "needs_approval": risk >= 3})
    return {"agent_id": agent["agent_id"], "role": role, "tools": tools}


@router.post(INBOUND_PREFIX + "tools/{tool}", summary="Agent ngoài gọi thẳng một công cụ (qua cổng chính sách)", tags=_TAG)
async def inbound_call_tool(tool: str, body: ToolBody, agent: Dict[str, Any] = Depends(inbound_agent)) -> Dict[str, Any]:
    from mateai.application.agent.tool_gate import run_tool_with_policy
    who = _principal(agent)
    gate = await run_tool_with_policy(tool, dict(body.arguments), caller=who, source_device=who,
                                      query=f"Agent «{agent['name']}» gọi trực tiếp {tool}", session_id=f"{who}:direct")
    return gate["result"]
