# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/enterprise.py
============================================
API doanh nghiệp: KPI, tài chính, chấm công, RAG/Graph-RAG, phân tích, onboarding,
HITL (Plugin Registry), connector & nguồn dữ liệu, tệp tải về, tác vụ nền, webhook.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.infrastructure.files.file_export import (
    _content_disposition,
    _rows_to_csv,
    _rows_to_xlsx,
    _safe_filename,
)
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()



@router.get(
    "/api/v1/enterprise/kpi-overview",
    summary="Phase 56: CEO Executive KPI Dashboard (Tổng quan KPI Doanh nghiệp)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_kpi_overview(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy toàn bộ chỉ số KPI: Tasks, Nhân sự, Tài chính, Cashflow, Burn Rate, Runway."""
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        from mateai.application.security.security_guard import scope_rows, security_guard
        overview = await run_blocking(erp_db.get_company_kpi_overview)
        who = await run_blocking(security_guard.principal, employee_id=current_user.get("username"))
        leaderboard = scope_rows(await run_blocking(erp_db.get_task_leaderboard, limit=5), who)
        if who["clearance"] < 3:      # tài chính là CONFIDENTIAL (prompt cuối §62)
            overview["finances"] = None
            overview["finances_note"] = "Số liệu tài chính cần cấp bảo mật 3."
        overview["leaderboard"] = leaderboard
        return {"status": "success", "data": overview}
    except Exception as e:
        logger.error("KPI overview error: %s", e)
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/finances",
    summary="Phase 56: Sổ quỹ Tài chính Doanh nghiệp (Danh sách giao dịch thu/chi)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_finances(
    limit: int = 50,
    finance_type: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách giao dịch tài chính (lọc theo loại: income/expense). CONFIDENTIAL:
    cần cấp bảo mật 3 (prompt cuối §62) — trước đây mọi tài khoản viewer đọc được sổ quỹ."""
    from mateai.application.security.security_guard import security_guard
    from mateai.infrastructure.database.erp_database import erp_db
    who = await run_blocking(security_guard.principal, employee_id=current_user.get("username"))
    if who["clearance"] < 3:
        raise HTTPException(status_code=403, detail="Sổ quỹ là dữ liệu mật — cần cấp bảo mật 3.")
    try:
        records = await run_blocking(erp_db.get_finances, finance_type=finance_type, limit=limit)
        summary = await run_blocking(erp_db.get_financial_summary)
        return {"status": "success", "total": len(records), "summary": summary, "records": records}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/finances/record",
    summary="Phase 56: Ghi nhận giao dịch tài chính (Thu/Chi)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_record_finance(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Ghi nhận khoản thu hoặc chi vào Sổ quỹ doanh nghiệp.

    Zero-Trust: đi qua cổng HITL với tên tác vụ `record_income` (Level 3) hoặc
    `record_expense` (Level 4, tự nâng lên Level 5 nếu số tiền >= 50 triệu).
    Việc đội tên tác vụ vào đúng tên trong RISK_LEVEL_MAP là bắt buộc — nếu dùng
    tên chung chung thì ngưỡng 50 triệu sẽ không kích hoạt.
    """
    from mateai.application.enterprise.operations import UseCaseError, record_finance
    try:
        return await record_finance(current_user, await request.json())
    except UseCaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/attendance",
    summary="Phase 56: Tra cứu dữ liệu Chấm công",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_attendance(
    date_str: Optional[str] = None,
    limit: int = 50,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách chấm công theo ngày."""
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        from mateai.application.security.security_guard import scope_rows, security_guard
        who = await run_blocking(security_guard.principal, employee_id=current_user.get("username"))
        records = scope_rows(await run_blocking(erp_db.get_attendance, date_str=date_str, limit=limit), who)
        return {"status": "success", "date": date_str or "Hôm nay", "total": len(records), "records": records}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/leaderboard",
    summary="Phase 56: Bảng xếp hạng thi đua năng suất nhân viên",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_leaderboard(
    limit: int = 10,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy bảng xếp hạng hoàn thành công việc (Employee Leaderboard)."""
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        from mateai.application.security.security_guard import scope_rows, security_guard
        who = await run_blocking(security_guard.principal, employee_id=current_user.get("username"))
        leaderboard = scope_rows(await run_blocking(erp_db.get_task_leaderboard, limit=limit), who)
        return {"status": "success", "leaderboard": leaderboard}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/standup-briefing",
    summary="Phase 56: Họp Giao Ban Tự Động (Executive Daily Standup Briefing)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_standup(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Tạo báo cáo Giao Ban Tự Động tóm tắt tình hình toàn công ty 24h qua."""
    try:
        from mateai.application.agent.agent_orchestrator import multi_agent_system
        result = multi_agent_system.get_executive_briefing()
        return {"status": "success", "briefing": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/rag/query",
    summary="Phase 56: Tra cứu Tri thức Công ty qua Enterprise RAG",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_query(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Tra cứu nội quy, SOP và chính sách công ty từ ChromaDB Knowledge Base."""
    try:
        body = await request.json()
        question = body.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="Thiếu tham số: question")
        from mateai.application.knowledge import rag_acl
        from mateai.application.knowledge.rag_engine import rag_engine
        with rag_acl.use_reader(current_user.get("username")):   # chỉ trả tài liệu người hỏi được phép xem
            result = await run_blocking(rag_engine.answer_policy_question, question=question)  # ChromaDB: ngoài loop
        return {
            "status": "success",
            "result": result,
            # Phase 58 BƯỚC 4 (graceful fallback): frontend dựa vào cờ này để
            # TỰ ĐỘNG mở popup upload khi AI không tìm thấy căn cứ.
            "needs_document": bool(result.get("needs_document")),
            "suggested_action": result.get("suggested_action", ""),
            # Bản tiếng Việt để UI hiện cho người đọc, tách khỏi mã máy ở trên.
            "suggested_action_text": result.get("suggested_action_text", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/rag/upload",
    summary="Phase 58: Tải tài liệu lên tri thức doanh nghiệp (PDF/Word/MD/CSV)",
    tags=["Enterprise OS Phase 58"],
)
async def api_enterprise_rag_upload(
    file: UploadFile = File(...),
    category: str = Form("Tài liệu công ty"),
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Nhận tệp tải lên trực tiếp từ trình duyệt, lưu vào thư mục tri thức rồi
    nạp vào cả ChromaDB (vector) lẫn Knowledge Graph.

    Đây là endpoint mà `rag/ingest` chỉ có thể gọi gián tiếp — trước đó nhánh
    "AI bảo Sếp tải tài liệu lên" (graceful fallback của Phase 58 BƯỚC 4) không
    có chỗ để tải vì chỉ tồn tại đường nhận `file_path` phía server.

    An toàn:
      - Chỉ nhận phần mở rộng trong danh sách cho phép (chống tải lên .py/.sh...).
      - Tên tệp được chuẩn hoá, không giữ đường dẫn do client gửi.
      - `content_type` phải khớp phần mở rộng.
    """
    from mateai.application.knowledge import rag_engine as rag

    payload = await file.read()
    try:
        dest = rag.save_upload(file.filename or "", payload)
    except rag.UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    safe_name = dest.name
    original_name = os.path.basename(file.filename or "").strip()

    logger.info(
        "[RAG Upload] %s (%s) tải lên %s — %d bytes, mục '%s'",
        current_user.get("username", "?"), original_name, safe_name, len(payload), category,
    )

    try:
        # Parse PDF/Word -> chunk -> embed là việc nặng, đồng bộ: chạy ngoài event loop
        # (trước đây gọi thẳng trong hàm async — mọi kênh đứng trong lúc nạp).
        result = await run_blocking(rag.rag_engine.ingest_file, file_path=dest, category=category)
    except Exception as exc:
        logger.error("[RAG Upload] Lỗi nạp %s: %s", safe_name, exc)
        return {"status": "error", "error": str(exc), "saved_as": safe_name}

    # Audit. Trước đây gọi `erp_db.log_audit_action` mà router KHÔNG import `erp_db` —
    # NameError bị `except: pass` nuốt, nên chưa lần tải tài liệu nào được ghi audit.
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(current_user.get("username", "?")), "KNOWLEDGE_UPLOAD", "DATA",
                                  "SUCCESS" if result.get("status") == "success" else "FAILED",
                                  {"original_name": original_name, "saved_as": safe_name,
                                   "bytes": len(payload), "category": category})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RAG Upload] Không ghi được audit: %s", exc)

    if result.get("status") != "success":
        raise HTTPException(
            status_code=422,
            detail=result.get("message", "Không đọc được nội dung tài liệu."),
        )

    return {"status": "success", "result": result, "saved_as": safe_name}


@router.post(
    "/api/v1/enterprise/rag/ingest",
    summary="Phase 56: Nạp tài liệu mới vào ChromaDB Knowledge Base",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_ingest(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Nạp và vector hóa tài liệu nội bộ (PDF/Word/MD) vào tri thức doanh nghiệp.

    Zero-Trust: `file_path` là dữ liệu do client kiểm soát nên bắt buộc phải đi
    qua resolve_ingest_path() để chống path traversal. Chỉ nhận tệp nằm trong
    storage/knowledge_docs/ (nơi endpoint upload đặt file lên).

    Phase 60: chạy nền thay vì chặn request. `ingest_file()` là hàm đồng bộ
    nặng (parse PDF/Word -> chunk -> embed); gọi trực tiếp trong `async def` sẽ
    giữ chân event loop, khiến mọi request khác — kể cả luồng streaming của
    kiosk — phải xếp hàng chờ. Nay trả `task_id` ngay và đẩy việc vào
    `BackgroundWorkerManager`; tiến độ xem ở
    `GET /api/v1/enterprise/background-tasks`.
    """
    try:
        body = await request.json()
        doc_path = body.get("file_path", "")
        category = body.get("category", "Tài liệu công ty")
        from mateai.application.knowledge.rag_engine import rag_engine

        safe_path, path_error = rag_engine.resolve_ingest_path(doc_path)
        if path_error:
            logger.warning(
                "[RAG Ingest] Từ chối nạp tài liệu từ %s: file_path=%r (%s)",
                current_user.get("username", "?"), str(doc_path)[:120], path_error,
            )
            raise HTTPException(status_code=400, detail=path_error)

        from mateai.application.operations.background_workers import background_worker_manager

        # Task name theo tên tệp: cùng một tệp đang nạp thì dedupe (trả lại task
        # cũ), tệp khác thì chạy song song.
        stem = Path(safe_path).stem or "document"
        task_name = f"rag_ingest_{stem}"

        task_id = await background_worker_manager.submit(
            task_name,
            rag_engine.ingest_file,
            safe_path,
            category=category,
            description=f"Nạp tài liệu vào tri thức doanh nghiệp: {stem}",
            metadata={
                "file_path": safe_path,
                "category": category,
                "requested_by": current_user.get("username", "?"),
            },
            # Không phát TTS: đây là thao tác quản trị, không phải hội thoại
            # với người dùng; bật sẽ phát giọng lạc vào phòng họp.
            notify_on_complete=False,
        )
        return {
            "status": "success",
            "queued": True,
            "task_id": task_id,
            "task_name": task_name,
            "message": (
                "Đã đưa vào hàng đợi nền. Theo dõi tại "
                "GET /api/v1/enterprise/background-tasks"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/rag/documents",
    summary="Phase 56: Danh sách tài liệu trong Knowledge Base",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_docs(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách tài liệu đã được vector hóa trong ChromaDB."""
    from mateai.application.knowledge import rag_acl
    from mateai.application.knowledge.rag_engine import rag_engine
    try:
        with rag_acl.use_reader(current_user.get("username")):
            docs = rag_engine.list_documents()
        return {"status": "success", "total": len(docs), "documents": docs}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/rag/acl",
    summary="Quyền đọc theo tài liệu của kho tri thức (mức phân loại + phòng ban)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_acl_list(current_user: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.knowledge import rag_acl
    from mateai.application.knowledge.rag_engine import rag_engine
    acls = rag_acl.list_acls()
    with rag_acl.use_reader(current_user.get("username")):
        names = [d["name"] for d in rag_engine.list_documents()]
    return {"classifications": list(rag_acl.CLASSIFICATIONS), "default": rag_acl.DEFAULT_CLASSIFICATION,
            "documents": [acls.get(n) or rag_acl.get_acl(n) for n in names]}


@router.put(
    "/api/v1/enterprise/rag/acl/{doc_name}",
    summary="Đặt mức phân loại / phòng ban được xem cho một tài liệu",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_acl_set(
    doc_name: str, body: Dict[str, Any] = Body(...), current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    from mateai.application.knowledge import rag_acl
    try:
        return rag_acl.set_acl(doc_name, body.get("classification", ""), body.get("departments") or [], str(current_user.get("username") or "admin"))
    except rag_acl.AclError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete(
    "/api/v1/enterprise/rag/acl/{doc_name}",
    summary="Đưa tài liệu về quyền mặc định (INTERNAL, mọi phòng ban)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_acl_reset(doc_name: str, current_user: Dict[str, Any] = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.knowledge import rag_acl
    if not rag_acl.reset_acl(doc_name, str(current_user.get("username") or "admin")):
        raise HTTPException(status_code=404, detail="Tài liệu đang dùng quyền mặc định")
    return {"status": "success"}


@router.post(
    "/api/v1/enterprise/multi-agent/route",
    summary="Phase 57: CEO Router — Điều phối yêu cầu tới Multi-Agent System",
    tags=["Enterprise OS Phase 57"],
)
async def api_multi_agent_route(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Ủy quyền câu hỏi/chỉ thị tới hệ thống Multi-Agent (CFO/HR/CTO Agent).

    Zero-Trust (Phase 84): `delegate_to_multi_agent` ở Level 2 — "thao tác
    thường", chạy thẳng, KHÔNG bắt CEO duyệt từng câu hỏi Multi-Agent nữa
    (trước đây kể cả câu chỉ đọc cũng phải duyệt, CEO nhận tin liên tục).
    Tác vụ thật sự nguy hiểm vẫn đi qua cổng HITL ở đúng tool của chúng
    (record / delete / run_powershell...).
    """
    from mateai.application.enterprise.operations import UseCaseError, delegate_multi_agent
    try:
        return await delegate_multi_agent(current_user, (await request.json()).get("query", ""))
    except UseCaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/analytics/chart",
    summary="Phase 57: Text-to-SQL Dynamic Chart Generation (Vẽ biểu đồ BI theo lệnh tự nhiên)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_generate_chart(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Chuyển đổi câu lệnh tiếng Việt thành SQL + sinh cấu hình Chart.js.

    Phase 58 BƯỚC 2: ngoài trả HTTP, biểu đồ còn được **phát sóng qua
    WebSocket** tới mọi màn hình đang mở. Trước đây chỉ người gọi API nhận
    được — nên khi CEO ra lệnh bằng giọng nói từ điện thoại, màn hình trong
    phòng họp không có gì hiện ra, dù đã có sẵn dữ liệu và cấu hình biểu đồ.
    """
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            return {"status": "error", "error": "Thiếu tham số: prompt"}
        from mateai.application.analytics.analytics_engine import analytics_engine
        # Gọi LLM đồng bộ (tới 60 s/model) + SQL — không được chạy trên event loop.
        result = await run_blocking(analytics_engine.text_to_sql_and_chart, prompt=prompt)

        # Chỉ phát sóng khi thực sự có biểu đồ. Không có client nào đang mở
        # thì `broadcast_portal_ui` tự thoát ngay, không tốn chi phí.
        if result.get("chart_config") and result.get("status") == "success":
            try:
                await broadcast_portal_ui(
                    "analytics_chart",
                    {
                        "chart_config": result["chart_config"],
                        "title": result.get("title", ""),
                        "sql": result.get("sql", ""),
                        "sql_source": result.get("sql_source", ""),
                        # Mang cả lỗi LLM xuống UI: khi SQL rơi về dự phòng,
                        # người xem phải thấy "chế độ dự phòng" chứ không tưởng
                        # đó là câu trả lời đúng câu hỏi.
                        "llm_error": result.get("llm_error"),
                        "records_count": result.get("records_count", 0),
                        "requested_by": current_user.get("username", "unknown"),
                    },
                )
            except Exception as bs_exc:
                # Lỗi broadcast KHÔNG được làm hỏng HTTP response — người gọi
                # vẫn cần nhận được biểu đồ qua REST.
                logger.warning("[Server] Broadcast biểu đồ thất bại (bỏ qua): %s", bs_exc)

        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/analytics/cashflow-health",
    summary="Phase 57: Predictive Cashflow Health Check (Cảnh báo dòng tiền dự báo)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_cashflow_health(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Chạy thuật toán dự báo Burn Rate & Runway. Phát CẢNH BÁO ĐỎ nếu quỹ sắp cạn."""
    from mateai.application.analytics.analytics_engine import analytics_engine
    try:
        result = await run_blocking(analytics_engine.evaluate_predictive_cashflow)

        # Phase 58 BƯỚC 2: đẩy trạng thái dòng tiền lên mọi màn hình để
        # Command Center tự đổi sang chế độ đỏ mà không cần ai bấm F5.
        try:
            await broadcast_portal_ui(
                "cashflow_health",
                {
                    "alert_level": result.get("alert_level"),
                    "is_critical": result.get("is_critical"),
                    "is_warning": result.get("is_warning"),
                    "runway_days": result.get("runway_days"),
                    "net_balance": result.get("net_balance"),
                    "message": result.get("message", ""),
                },
            )
        except Exception as bs_exc:
            logger.warning("[Server] Broadcast cashflow-health thất bại (bỏ qua): %s", bs_exc)

        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/graph-rag/query",
    summary="Phase 57: GraphRAG Knowledge Graph Hybrid Search",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_graph_rag(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Truy vấn Đồ thị Tri thức doanh nghiệp (GraphRAG) kết hợp BM25 + Vector Semantic Search."""
    try:
        body = await request.json()
        question = body.get("question", "")
        if not question:
            return {"status": "error", "error": "Thiếu tham số: question"}
        from mateai.application.knowledge import rag_acl
        from mateai.application.knowledge.graph_rag import graph_rag
        with rag_acl.use_reader(current_user.get("username")):
            result = await run_blocking(graph_rag.hybrid_search, question=question)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/onboarding",
    summary="Phase 57: Zero-Touch Employee Onboarding (Tự động hóa nhân sự mới)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_onboarding(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Kích hoạt luồng Onboarding tự động cho nhân viên mới.

    Zero-Trust — chống leo thang đặc quyền qua tham số: `role` trong body là
    dữ liệu do client kiểm soát. Trước đây truyền thẳng xuống workflow, nên
    bất kỳ tài khoản manager nào cũng có thể tạo nhân viên với
    role="admin"/"it_support". Nay chỉ ADMIN được tự chọn role; manager bị ép
    về "operator" và được ghi log cảnh báo.

    Đồng thời đi qua cổng HITL (`zero_touch_onboard_employee` = Level 4) vì đây
    là hành động cấp danh tính cho con người — theo briefing BƯỚC 5 thuộc nhóm
    3-5. Trước đây chỉ có RBAC nên manager tự tạo nhân viên không ai hỏi.
    """
    from mateai.application.enterprise.operations import UseCaseError, onboard_employee
    try:
        return await onboard_employee(current_user, await request.json())
    except UseCaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/email-gateway/simulate",
    summary="Phase 57: Giả lập tiếp nhận Email từ Khách hàng (Omnichannel Gateway)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_email_simulate(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Mô phỏng email khách hàng gửi đến. AI tự phân loại, tạo Ticket và gửi auto-reply."""
    try:
        body = await request.json()
        from mateai.interfaces.email.email_gateway import email_gateway
        # SMTP (tới 10 s) + ghi DB: ngoài event loop.
        result = await run_blocking(
            email_gateway.process_incoming_email,
            sender=body.get("sender", "khachhang@doanhnghiep.vn"),
            subject=body.get("subject", "Yêu cầu hỗ trợ"),
            content=body.get("content", ""),
        )
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/hitl/pending",
    summary="Phase 57: Zero-Trust HITL — Danh sách tác vụ rủi ro cao đang chờ CEO duyệt",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_pending_list(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách các yêu cầu Risk Level 4-5 đang chờ Human-in-the-Loop CEO phê duyệt."""
    from mateai.application.security.zero_trust import hitl_manager
    try:
        pending = hitl_manager.get_pending_list()
        return {"status": "success", "total_pending": len(pending), "pending_approvals": pending}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/hitl/approve",
    summary="Phase 57: Zero-Trust HITL — CEO phê duyệt tác vụ rủi ro cao",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_approve(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """CEO xác nhận DUYỆT thực thi tác vụ đang trong hàng chờ HITL."""
    try:
        body = await request.json()
        approval_id = body.get("approval_id", "")
        if not approval_id:
            return {"status": "error", "error": "Thiếu approval_id"}
        from mateai.application.security.zero_trust import hitl_manager
        # `approve_async()` chạy đúng cả hai loại tác vụ:
        #   - coroutine  -> await trực tiếp (skill gọi qua API)
        #   - hàm sync   -> asyncio.to_thread (ghi DB, gọi API ngoại vi),
        #                   nên tác vụ chậm không giữ chân event loop và làm
        #                   đứng mọi request khác kể cả /health.
        #
        # Trước đây gọi thẳng `hitl_manager.approve(...)` ngay trên event
        # loop: một lần tự khoá chết trong `add_finance_record` đã TREO CẢ
        # SERVER, và executor dạng coroutine thì không bao giờ chạy dù vẫn
        # báo `executed: true`.
        result = await hitl_manager.approve_async(
            approval_id=approval_id,
            approved_by=current_user.get("username", "CEO"),
        )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/hitl/reject",
    summary="Phase 57: Zero-Trust HITL — CEO từ chối / hủy tác vụ rủi ro cao",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_reject(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """CEO xác nhận TỪ CHỐI tác vụ đang trong hàng chờ HITL."""
    try:
        body = await request.json()
        approval_id = body.get("approval_id", "")
        reason = body.get("reason", "Bị từ chối bởi CEO")
        if not approval_id:
            return {"status": "error", "error": "Thiếu approval_id"}
        from mateai.application.security.zero_trust import hitl_manager
        # `reject()` cũng ghi audit log — đẩy sang thread như `approve()`
        # để việc ghi DB không giữ chân event loop.
        result = await asyncio.to_thread(
            hitl_manager.reject,
            approval_id=approval_id,
            rejected_by=current_user.get("username", "CEO"),
            reason=reason,
        )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/proactive/run-audit",
    summary="Phase 56: Kích hoạt quét đôn đốc tiến độ task ngay lập tức",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_proactive_audit(
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt Virtual C.O.O rà soát ngay tất cả task quá hạn và sắp đến hạn."""
    try:
        from mateai.application.skills.builtin.proactive_manager import proactive_manager
        result = await run_blocking(proactive_manager.execute_task_audit_sync, trigger_source="api_manual")
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/connectors/health",
    summary="Phase 59: Trạng thái 4 connector ngoại vi (AWS, OCI, Paperless, eInvoice)",
    tags=["Enterprise OS Phase 59"],
)
async def api_connectors_health(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Báo cáo trạng thái cấu hình + độ trễ của từng connector.

    KHÔNG gọi `authenticate()` — chỉ đọc cấu hình trong bộ nhớ, nên phản hồi
    tức thì và không tạo tải lên AWS/OCI. Muốn kiểm tra thật thì dùng skill
    `check_connector_health` (đi qua cổng HITL).
    """
    from mateai.application.enterprise.integrations import connector_health
    return connector_health()


@router.get(
    "/api/v1/enterprise/connectors/catalog",
    summary="Danh mục connector + JSON Schema cấu hình (Admin No-code form)",
    tags=["Enterprise OS Phase 59"],
)
async def api_connectors_catalog(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Danh mục đầy đủ connector cho trang Quản trị, kèm schema cấu hình.

    Khác `.../connectors/health` ở chỗ endpoint này gom luôn metadata hiển thị và
    JSON Schema, nên giao diện chỉ cần một vòng gọi là dựng được cả lưới card lẫn
    form cấu hình. `.../connectors/health` vẫn giữ nguyên cho tab Tích hợp
    Hệ Thống của portal.

    Danh sách lấy đúng từ CONNECTOR_REGISTRY — nếu không có connector trong
    registry thì không hiện, để trang quản trị không hứa ra thứ hệ thống
    không có.
    """
    from mateai.application.enterprise.integrations import connector_catalog
    return connector_catalog()


@router.get(
    "/api/v1/enterprise/data-sources",
    summary="Phase 62: Danh sách data source tùy chỉnh (đã che secret)",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_list(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Trả về data source người dùng đã khai báo, kèm 4 connector có sẵn.

    Secret KHÔNG bao giờ nằm trong phản hồi — `custom_registry.mask_source`
    thay `auth_value` bằng cờ `has_auth`. Endpoint này cũng gom sẵn 4 connector
    Phase 59 để UI chỉ cần một lệnh gọi cho toàn bộ danh sách nguồn dữ liệu.
    """
    from mateai.application.enterprise.integrations import data_sources_overview
    return data_sources_overview()


@router.get(
    "/api/v1/enterprise/data-sources/presets",
    summary="Mẫu khai báo hạ tầng phổ biến (Prometheus, Zabbix, vCenter, GLPI…)",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_presets(
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Mẫu viết theo tài liệu hãng, `verified=false` cho tới khi người vận hành thử trên hệ thống thật."""
    from mateai.infrastructure.connectors.presets import catalog
    return {"presets": catalog()}


@router.post(
    "/api/v1/enterprise/data-sources",
    summary="Phase 62: Tạo/cập nhật data source tùy chỉnh",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_upsert(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Tạo mới hoặc cập nhật một data source.

    `id` lấy từ payload. Ô `auth_value` để trống nghĩa là GIỮ khoá đang lưu
    (giống form cấu hình connector) — nên sửa `title` không buộc phải nhập lại
    mật khẩu. Trả 400 kèm lý do cụ thể khi khai báo không dùng được, vì người
    vận hành cần biết sửa ô nào chứ không phải một lỗi chung chung.
    """
    try:
        from mateai.infrastructure.connectors import custom_registry

        source_id = str(payload.get("id") or "").strip().lower()
        record = custom_registry.upsert_source(source_id, payload)
        return {"status": "success", "data_source": record}
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] upsert data source lỗi")
        return {"status": "error", "error": str(e)}


@router.delete(
    "/api/v1/enterprise/data-sources/{source_id}",
    summary="Phase 62: Xoá data source tùy chỉnh",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_delete(
    source_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Xoá một data source. Chỉ admin — xoá là mất cấu hình, không hoàn lại được."""
    try:
        from mateai.infrastructure.connectors import custom_registry

        if not custom_registry.delete_source(source_id):
            return {"status": "error", "error": f"Không tìm thấy nguồn '{source_id}'"}
        return {"status": "success", "deleted": source_id}
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] xoá data source lỗi")
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/data-sources/{source_id}/probe",
    summary="Phase 62: Kiểm tra kết nối tới data source",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_probe(
    source_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Gọi thật 1 request tới app để xác nhận URL/khoá đúng.

    Endpoint này CỐ TÌNH gọi ra ngoài — khác `connectors/health` chỉ đọc cấu
    hình trong bộ nhớ. Nhưng chỉ manager+ mới gọi được, và request chỉ mang
    `limit=1` nên không kéo nặng app của khách.
    """
    try:
        from mateai.infrastructure.connectors import probe_data_source

        result = await probe_data_source(source_id)
        return {
            "status": "success",
            "healthy": result.success,
            "latency_ms": round(result.latency_ms, 1),
            "error": result.error,
            "detail": result.data,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] probe data source lỗi")
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/data-sources/{source_id}/fetch",
    summary="Phase 62: Lấy dữ liệu báo cáo từ data source",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_fetch(
    source_id: str,
    payload: Optional[Dict[str, Any]] = None,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Kéo dữ liệu báo cáo, chuẩn hoá về `{rows, columns, total}`.

    `payload` tuỳ chọn: `{"path": "doanh thu"}` (tên path đã khai báo hoặc URL
    tương đối), `{"query": {...}}`, `{"limit": 100}`. Không có path thì dùng
    `default_path` của khai báo.
    """
    try:
        from mateai.infrastructure.connectors import fetch_data_source

        result = await fetch_data_source(source_id, payload or {})
        if not result.success:
            return {
                "status": "error",
                "error": result.error,
                "latency_ms": round(result.latency_ms, 1),
            }

        return {
            "status": "success",
            "data": result.data,
            "latency_ms": round(result.latency_ms, 1),
            "metadata": result.metadata,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] fetch data source lỗi")
        return {"status": "error", "error": str(e)}


@router.post(
    "/api/v1/enterprise/data-sources/{source_id}/export",
    summary="Phase 62: Xuất dữ liệu báo cáo ra Excel (.xlsx) hoặc CSV",
    tags=["Enterprise OS Phase 62"],
)
async def api_data_sources_export(
    source_id: str,
    payload: Optional[Dict[str, Any]] = None,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
):
    """
    Kéo dữ liệu rồi trả về file để tải xuống.

    Xuất ở server chứ không ở trình duyệt vì hai lý do thực tế:
      - CSV do Excel mở cần BOM UTF-8, nếu không tiếng Việt thành ký tự lỗi.
        Sửa encoding ở tay người dùng thì rất dễ quên và phải làm lại mỗi lần.
      - File sinh ở server không cần thêm thư viện ~1MB vào trang.

    Trả về `Response` dạng attachment; lỗi trả JSON 502 để UI hiện toast.
    """
    from fastapi.responses import Response as FastAPIResponse

    body = payload or {}
    fmt = str(body.get("format") or "xlsx").strip().lower()
    if fmt not in ("xlsx", "csv"):
        return {"status": "error", "error": "format chỉ nhận 'xlsx' hoặc 'csv'"}

    try:
        from mateai.infrastructure.connectors import fetch_data_source

        # Số dòng xuất mặc định cao hơn xem trước: xuất là để đưa đi xử lý,
        # nên lấy nhiều hơn con số 8 dòng hiện trên màn hình.
        export_limit = _safe_int(body.get("limit"), 1000, 1, 10000)
        params = {k: v for k, v in body.items() if k in ("path", "query", "method")}
        params["limit"] = export_limit

        result = await fetch_data_source(source_id, params)
        if not result.success:
            return {"status": "error", "error": result.error}

        data = result.data or {}
        rows = data.get("rows") or []
        columns = data.get("columns") or (list(rows[0].keys()) if rows else [])
        if not rows:
            return {"status": "error", "error": "Nguồn dữ liệu không có bản ghi nào để xuất"}

        # Tên file có dấu tiếng Việt, nhưng header HTTP chỉ chứa ASCII an toàn —
        # RFC 5987 dùng `filename*` để mang tên gốc, `filename` là bản ASCII
        # dự phòng cho client cũ.
        title = _safe_filename(body.get("title") or source_id)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
        if fmt == "csv":
            content = _rows_to_csv(rows, columns)
            return FastAPIResponse(
                content=content,
                media_type="text/csv; charset=utf-8",
                headers={
                    # BOM để Excel nhận UTF-8; không có nó, tiếng Việt ra
                    # "Ã¡" hay "?" tuỳ phiên bản.
                    "Content-Disposition": _content_disposition(title, stamp, "csv"),
                    "X-Content-Type-Options": "nosniff",
                },
            )

        content = _rows_to_xlsx(rows, columns, title)
        return FastAPIResponse(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": _content_disposition(title, stamp, "xlsx"),
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase62] export data source lỗi")
        return {"status": "error", "error": str(e)}


def _safe_int(value: Any, default: int, lo: int, hi: int) -> int:
    """Ép về số nguyên trong khoảng an toàn, trả mặc định nếu rác."""
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@router.get(
    "/api/v1/enterprise/downloads/pending",
    summary="Phase 64: Các file AI đã dựng, chờ giao diện tải",
    tags=["Enterprise OS Phase 64"],
)
async def api_downloads_pending(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Giao diện poll endpoint này sau mỗi câu trả lời của AI.

    Chỉ trả metadata, KHÔNG kèm nội dung file: dữ liệu báo cáo phải đi qua
    endpoint tải có xác thực, không nằm lẫn trong phản hồi poll.
    """
    from mateai.infrastructure.files.download_queue import download_queue

    jobs = download_queue.pending()
    return {"status": "success", "jobs": jobs, "count": len(jobs)}


@router.get(
    "/api/v1/enterprise/downloads/{job_id}",
    summary="Phase 64: Tải file AI đã dựng",
    tags=["Enterprise OS Phase 64"],
)
async def api_download_file(
    job_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
):
    """
    Trả file về máy và XOÁ job khỏi hàng đợi ngay lúc lấy.

    Xoá sớm là cố ý: job chỉ tải được một lần. Đổi lại không có dữ liệu báo
    cáo nào nằm lại trong RAM quá thời hạn.
    """
    from mateai.infrastructure.files.download_queue import download_queue

    job = download_queue.take(job_id)
    if job is None:
        # Trả 404 chứ không phải 403: job hết hạn và job không tồn tại là cùng
        # một việc với người dùng, và phân biệt chỉ giúp kẻ đoán mò.
        return {"status": "error", "error": "File đã hết hạn hoặc đã được tải"}

    from fastapi.responses import Response as FastAPIResponse

    media = (
        "text/csv; charset=utf-8"
        if job.format == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FastAPIResponse(
        content=job.payload,
        media_type=media,
        headers={
            # `job.filename` đã đi qua `_safe_filename` nên chỉ còn ASCII an
            # toàn; header này không cần `filename*` vì tên đã bỏ dấu.
            "Content-Disposition": f'attachment; filename="{job.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/api/v1/enterprise/downloads/clear",
    summary="Phase 64: Xoá toàn bộ file đang chờ tải",
    tags=["Enterprise OS Phase 64"],
)
async def api_downloads_clear(
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Bỏ mọi job đang chờ. Chỉ admin — dùng khi đổi máy hoặc cần dọn."""
    from mateai.infrastructure.files.download_queue import download_queue

    n = download_queue.drop_all()
    return {"status": "success", "cleared": n}


@router.get(
    "/api/v1/enterprise/plugin-registry/stats",
    summary="Phase 60: Thống kê Plugin Registry + trạng thái circuit breaker",
    tags=["Enterprise OS Phase 60"],
)
async def api_plugin_registry_stats(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Số tool đã đăng ký, số tool đang bật, và thống kê thực thi theo tool."""
    try:
        from mateai.application.skills.plugin_registry import plugin_registry

        with plugin_registry._lock:  # đọc snapshot nhất quán
            tools = {
                name: {
                    "description": t.description,
                    "enabled": t.enabled,
                    "risk_level": t.risk_level,
                    "is_async": t.is_async,
                }
                for name, t in plugin_registry._tools.items()
            }
        stats = plugin_registry.get_stats()
        return {
            "status": "success",
            "total_tools": len(tools),
            "enabled_tools": sum(1 for t in tools.values() if t["enabled"]),
            "tools": tools,
            "execution_stats": stats,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/background-tasks",
    summary="Phase 60: Danh sách tác vụ nền đang chạy / đã hoàn tất",
    tags=["Enterprise OS Phase 60"],
)
async def api_background_tasks(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Liệt kê tác vụ nền (Phase 60) kèm tiến độ. Chỉ đọc bộ nhớ."""
    try:
        from mateai.application.operations.background_workers import TaskStatus, background_worker_manager

        tasks = background_worker_manager.list_tasks()
        return {
            "status": "success",
            "total": len(tasks),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "tasks": [t.to_dict() for t in tasks[:50]],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get(
    "/api/v1/enterprise/webhooks/recent",
    summary="Phase 59: 20 cảnh báo webhook gần nhất",
    tags=["Enterprise OS Phase 59"],
)
async def api_recent_webhooks(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đọc lịch sử cảnh báo webhook đã ghi vào Proactive Manager.
    Nguồn sự thật vẫn là `proactive_manager._audit_history`; endpoint này chỉ
    lọc và đảo chiều để UI hiển thị mới nhất trước.
    """
    try:
        from mateai.application.skills.builtin.proactive_manager import proactive_manager

        history = list(getattr(proactive_manager, "_audit_history", []))
        hooks = [h for h in history if str(h.get("source", "")).startswith("webhook:")]
        hooks.reverse()
        return {"status": "success", "total": len(hooks), "alerts": hooks[:20]}
    except Exception as e:
        return {"status": "error", "error": str(e)}
