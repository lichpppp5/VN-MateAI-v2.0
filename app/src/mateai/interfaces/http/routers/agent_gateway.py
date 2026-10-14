# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/agent_gateway.py
===============================================
API cổng agent: khai báo MCP server ngoài, thử kết nối, đồng bộ công cụ, đặt rủi ro / bật tắt từng công cụ
(docs/integrations/agent-gateway.md). Xem: manager + admin. Mọi thay đổi: admin.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from mateai.application.agent_gateway import mcp_gateway as gw
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()
_READ = Depends(require_roles(["manager", "admin"]))
_WRITE = Depends(require_roles(["admin"]))
_TAG = ["Agent Gateway"]
_BASE = "/api/v1/agent-gateway/mcp/servers"


def _who(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "admin")


def _422(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get(_BASE, summary="Các MCP server đã khai báo + công cụ của từng server", tags=_TAG)
async def list_servers(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    from mateai.config.loader import settings
    return {"servers": gw.list_servers(), "allow_stdio": settings.agent_gateway.allow_stdio}


@router.get(_BASE + "/{server_id}", summary="Chi tiết một MCP server", tags=_TAG)
async def get_server(server_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    s = gw.get_server(server_id)
    if not s:
        raise HTTPException(status_code=404, detail="Không có MCP server này")
    return s


@router.put(_BASE + "/{server_id}", summary="Lưu khai báo MCP server (khoá xác thực được mã hoá)", tags=_TAG)
async def save_server(server_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return gw.save_server(server_id, body, _who(user))
    except gw.GatewayError as exc:
        raise _422(exc)


@router.delete(_BASE + "/{server_id}", summary="Xoá MCP server (gỡ mọi công cụ của nó)", tags=_TAG)
async def delete_server(server_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    if not gw.delete_server(server_id, _who(user)):
        raise HTTPException(status_code=404, detail="Không có MCP server này")
    return {"status": "success"}


@router.post("/api/v1/agent-gateway/mcp/test", summary="Thử kết nối một khai báo (chưa lưu, không đăng ký công cụ)", tags=_TAG)
async def test_server(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return await gw.test_connection(body)
    except gw.GatewayError as exc:
        raise _422(exc)


@router.post(_BASE + "/{server_id}/discover", summary="Kết nối, đọc lại danh sách công cụ và đăng ký", tags=_TAG)
async def discover(server_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return await gw.discover(server_id, _who(user))
    except gw.GatewayError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.patch(_BASE + "/{server_id}/tools/{tool}", summary="Bật / tắt hoặc đặt mức rủi ro (1–5) cho một công cụ", tags=_TAG)
async def set_tool(server_id: str, tool: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    enabled: Optional[bool] = body.get("enabled")
    risk: Optional[int] = body.get("risk")
    try:
        return gw.set_tool_override(server_id, tool, enabled, risk, _who(user))
    except (gw.GatewayError, TypeError, ValueError) as exc:
        raise _422(exc)
