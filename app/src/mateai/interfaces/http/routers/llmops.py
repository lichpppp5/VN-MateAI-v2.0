# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/llmops.py
========================================
API mức dùng AI, ngân sách và bộ đánh giá chất lượng (docs/ai/llmops.md).
Xem: manager + admin. Đặt ngân sách / lưu bộ kiểm / chạy đánh giá: admin.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from mateai.application.llmops import evaluation as ev
from mateai.application.llmops import usage
from mateai.interfaces.http.auth_dependencies import require_roles

router = APIRouter()
_READ = Depends(require_roles(["manager", "admin"]))
_WRITE = Depends(require_roles(["admin"]))
_TAG = ["LLMOps"]


def _who(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "admin")


@router.get("/api/v1/llmops/usage", summary="Mức dùng AI theo ngày / model / người + trạng thái ngân sách", tags=_TAG)
async def get_usage(days: int = Query(30, ge=1, le=365), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return usage.summary(days)


@router.get("/api/v1/llmops/budget", summary="Ngân sách AI hiện tại và mức đã dùng", tags=_TAG)
async def get_budget(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return usage.status()


@router.post("/api/v1/llmops/budget", summary="Đặt ngân sách AI (off | warn | block; 0 = không giới hạn)", tags=_TAG)
async def set_budget(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return usage.save_budget(_who(user), body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/llmops/eval/sets", summary="Các bộ kiểm chất lượng AI", tags=_TAG)
async def list_sets(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"sets": ev.list_sets()}


@router.put("/api/v1/llmops/eval/sets/{set_id}", summary="Lưu bộ kiểm (ca kiểm = câu hỏi + điều kiện đạt)", tags=_TAG)
async def save_set(set_id: str, body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return ev.save_set(set_id, body.get("name", ""), body.get("cases"), _who(user))
    except ev.EvalError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/llmops/eval/sets/{set_id}", summary="Chi tiết bộ kiểm", tags=_TAG)
async def get_set(set_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    s = ev.get_set(set_id)
    if not s:
        raise HTTPException(status_code=404, detail="Không có bộ kiểm này")
    return s


@router.delete("/api/v1/llmops/eval/sets/{set_id}", summary="Xoá bộ kiểm (lịch sử các lần chạy được giữ)", tags=_TAG)
async def delete_set(set_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    if not ev.delete_set(set_id, _who(user)):
        raise HTTPException(status_code=404, detail="Không có bộ kiểm này")
    return {"status": "success"}


@router.post("/api/v1/llmops/eval/sets/{set_id}/run", summary="Chạy bộ kiểm (nền; tốn token thật; chỉ công cụ chỉ đọc)", tags=_TAG)
async def run_set(set_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return await ev.start(set_id, _who(user))
    except ev.EvalError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/llmops/eval/runs", summary="Các lần chạy đánh giá", tags=_TAG)
async def list_runs(set_id: str = Query(""), limit: int = Query(30, ge=1, le=200), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"runs": ev.list_runs(set_id or None, limit)}


@router.get("/api/v1/llmops/eval/runs/{run_id}", summary="Chi tiết một lần chạy + so với lần trước (ca bị tụt)", tags=_TAG)
async def get_run(run_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    r = ev.get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="Không có lần chạy này")
    return r
