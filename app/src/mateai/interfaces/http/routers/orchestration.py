# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/orchestration.py
===============================================
API Lõi điều phối (docs/integrations/orchestration.md). Router chỉ xác thực / phân quyền / dịch lỗi; logic ở application/orchestration.

- Quản lý + quản trị viên: xem đội agent, bảng điểm, phiếu; giao việc; huỷ / giao lại phiếu CỦA MÌNH.
- Chỉ quản trị viên: thêm / sửa / tạm dừng / xoá agent thực thi; xác nhận kết quả phiếu cần người; huỷ / giao lại phiếu của người khác.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from mateai.application.orchestration import contracts as C
from mateai.application.orchestration import registry as R
from mateai.application.orchestration import service as S
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()
_READ = Depends(require_roles(["manager", "admin"]))
_ADMIN = Depends(require_roles(["admin"]))
_TAG = ["Orchestration"]
_CODES = {"not_found": 404, "state": 409, "busy": 429, "kill_switch": 423, "no_executor": 422, "l5": 403, "invalid": 422}


def _who(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "unknown")


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, S.OrderError):
        return HTTPException(status_code=_CODES.get(exc.code, 422), detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _own_or_admin(order_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    o = S.get(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="Không có phiếu này")
    if user.get("role") != "admin" and o["requested_by"] != _who(user):
        raise HTTPException(status_code=403, detail="Chỉ người giao phiếu hoặc quản trị viên")
    return o


# ── đội agent ───────────────────────────────────────────────────────────────

@router.get("/api/v1/orchestration/executors", summary="Đội agent: thẻ năng lực, khả dụng, bảng điểm thật", tags=_TAG)
async def list_executors(days: int = Query(R.SCORE_WINDOW_DAYS, ge=1, le=365), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"executors": R.scoreboard(days), "window_days": days}


@router.get("/api/v1/orchestration/capabilities", summary="Năng lực → các agent nhận được", tags=_TAG)
async def capabilities(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"capabilities": R.capabilities_catalog()}


@router.get("/api/v1/orchestration/tools", summary="Công cụ có thể cấp cho agent nội bộ (chọn khi tạo vai)", tags=_TAG)
async def tools(user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    return {"tools": sorted(R.known_tools() - R.FORBIDDEN_LOCAL_TOOLS)}


@router.put("/api/v1/orchestration/executors/{executor_id}", summary="Thêm / sửa agent thực thi (tăng phiên bản, có audit)", tags=_TAG)
async def save_executor(executor_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    try:
        return R.save(executor_id, body, _who(user))
    except R.RegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/v1/orchestration/executors/{executor_id}/status", summary="Tạm dừng / bật lại một agent thực thi", tags=_TAG)
async def executor_status(executor_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    try:
        return R.set_status(executor_id, str(body.get("status") or ""), _who(user))
    except R.RegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/api/v1/orchestration/executors/{executor_id}", summary="Xoá agent thực thi (không được khi còn phiếu chưa xong)", tags=_TAG)
async def delete_executor(executor_id: str, user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    try:
        if not R.delete(executor_id, _who(user)):
            raise HTTPException(status_code=404, detail="Không có agent này")
    except R.RegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "deleted"}


# ── phiếu việc ──────────────────────────────────────────────────────────────

@router.post("/api/v1/orchestration/orders/preview", summary="CHẠY THỬ: phiếu hợp lệ không, giao cho ai, có cần duyệt không", tags=_TAG)
async def preview(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    try:
        return S.preview(body)
    except (C.ContractError, S.OrderError) as exc:
        raise _err(exc)


@router.post("/api/v1/orchestration/orders", summary="Giao việc: tạo phiếu (rủi ro ≥ 3 chờ duyệt)", tags=_TAG)
async def create_order(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    try:
        return await S.create(body, requested_by=_who(user), channel="portal", idempotency_key=body.get("idempotency_key"))
    except (C.ContractError, S.OrderError) as exc:
        raise _err(exc)


@router.get("/api/v1/orchestration/orders", summary="Phiếu việc gần đây", tags=_TAG)
async def list_orders(status: str = Query(""), executor_id: str = Query(""), limit: int = Query(100, ge=1, le=500),
                      user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"orders": S.list_orders(status or None, executor_id or None, limit)}


@router.get("/api/v1/orchestration/orders/{order_id}", summary="Chi tiết phiếu: định tuyến, báo cáo, thẩm định từng tiêu chí, lịch sử", tags=_TAG)
async def get_order(order_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    o = S.get(order_id)
    if not o:
        raise HTTPException(status_code=404, detail="Không có phiếu này")
    return o


@router.post("/api/v1/orchestration/orders/{order_id}/cancel", summary="Huỷ phiếu (agent ngoài có thể đã làm một phần)", tags=_TAG)
async def cancel_order(order_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    _own_or_admin(order_id, user)
    return S.cancel(order_id, _who(user))


@router.post("/api/v1/orchestration/orders/{order_id}/retry", summary="Giao lại = phiếu mới cùng nội dung (qua lại định tuyến + duyệt)", tags=_TAG)
async def retry_order(order_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    _own_or_admin(order_id, user)
    try:
        return await S.retry(order_id, _who(user))
    except (C.ContractError, S.OrderError) as exc:
        raise _err(exc)


@router.post("/api/v1/orchestration/orders/{order_id}/confirm", summary="Người xác nhận phiếu cần người: chấp nhận → xong, không → thất bại", tags=_TAG)
async def confirm_order(order_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    try:
        return S.confirm(order_id, _who(user), bool(body.get("accept", True)), str(body.get("note") or "")[:300])
    except S.OrderError as exc:
        raise _err(exc)
