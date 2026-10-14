# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/data_source_tools.py
================================
Phase 63 — Tool cho AI Ly Ly đọc và xuất báo cáo từ các nguồn dữ liệu tùy chỉnh.

Vấn đề
------
Phase 62/63 đã có đầy đủ khoản trống kỹ thuật: khai báo app doanh nghiệp bằng
form, kéo dữ liệu về chuẩn hoá `{rows, columns, total}`, xuất Excel/CSV. Nhưng
UI thì người dùng phải tự bấm. AI Ly Ly — phần được giao việc *"tra cứu và
lập báo cáo cho tôi"* — lại không thấy mảnh khối đó: danh sách tool chỉ có 11
tool Phase 59 (AWS/OCI/Paperless/eInvoice), không một tool nào chạm tới nguồn
tùy chỉnh. Hệ thống trả lời được câu hỏi về hạ tầng cloud mà không trả lời
được câu hỏi về ERP của chính công ty — lệch về mặt sản phẩm.

Ba tool, chia theo cách AI thực sự dùng
---------------------------------------
1. `list_data_sources`      — AI không biết công ty đã khai báo những gì. Gọi
                              cái này trước, không thì nó đoán mò tên.
2. `fetch_data_source`      — lấy dữ liệu để tự trả lời/tóm tắt. Đây là
                              tool dùng hằng ngày.
3. `prepare_data_source_export` — xuất file. Trả về *đường dẫn tải*, KHÔNG
                              trả bytes: một LLM không đọc nổi file .xlsx,
                              và nhét base64 vào ngữ cảnh chỉ tốn token rồi
                              bị bỏ qua. Việc tải là của người dùng.

Vì sao mọi tool đều đi qua cổng HITL
-----------------------------------
Các tool này gọi ra hệ thống bên ngoài — mạng của khách hàng, không phải của
bạn. Cho phép vòng lặp AI tự do gọi là mở đường để nó dò nhiều hệ thống ngoài
mà không ai duyệt. Đi qua `execute_with_hitl` giữ nguyên đúng mô hình Zero-Trust
sẵn có, và mỗi lần gọi đều để lại dấu vết trong nhật ký.

Bảo mật
-------
- Danh sách trả về KHÔNG có khoá bí mật (`mask_source` chỉ trả cờ `has_auth`).
  LLM không cần biết giá trị khoá, và đưa nó vào ngữ cảnh là tự tạo rủi ro rò
  rỉ qua log hội thoại.
- Số dòng bị chặn trần. Không có trần thì AI có thể kéo 1 triệu dòng về rồi
  làm vỡ ngữ cảnh.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

#: Trần số dòng cho một lần gọi từ AI. Bảng hàng trăm nghìn dòng đưa vào ngữ
#: cảnh sẽ làm vỡ cửa sổ ngữ cảnh và tốn kém — người dùng muốn file đầy đủ
#: thì dùng tool xuất, không phải để AI đọc hết vào đầu.
AI_ROW_LIMIT = 200


async def _run_with_hitl(
    action_name: str,
    params: Dict[str, Any],
    executor,
    description: str,
    flatten: bool = True,
):
    """
    Bọc executor qua cổng HITL rồi bóc envelope về kết quả phẳng.

    `execute_with_hitl` trả về `{status, risk_level, result: {...}}`. Đưa
    nguyên envelope cho LLM là sai: nó phải tự đào vào `result` mới thấy dữ
    liệu, dễ tưởng là lỗi và báo "không có kết quả". Ở đây bóc sẵn và, khi cần
    chờ duyệt, trả về đúng trạng thái để AI nói với người dùng là phải duyệt.

    Cùng cách `_execute_connector_action` trong `integration_tools.py` làm.
    """
    from mateai.application.security.zero_trust import execute_with_hitl

    result = await execute_with_hitl(
        action_name=action_name,
        params=params,
        executor=executor,
        requested_by="AI_Agent",
        agent_id="VN-MATEAI-CONNECTOR",
        check_rbac=False,
        description=description,
    )

    if not flatten:
        return result

    # Cần người dùng duyệt — AI phải nói rõ, không được báo "lỗi".
    if result.get("status") == "denied":
        return {"success": False, "error": result.get("message"), "policy_denied": True}
    if result.get("status") == "awaiting_approval":
        return {
            "success": False,
            "awaiting_approval": True,
            "approval_id": result.get("approval_id"),
            "risk_level": result.get("risk_level"),
            "message": (
                f"{result.get('message', 'Yêu cầu cần được duyệt.')} "
                "Hãy nói với người dùng rằng cần phê duyệt trong hàng đợi HITL, "
                "và cung cấp mã phiếu để họ đối chiếu."
            ),
        }

    return result.get("result") or {"success": False, "error": "Cổng HITL không trả về kết quả"}


@export_skill(
    name="list_data_sources",
    description=(
        "Liệt kê các nguồn dữ liệu doanh nghiệp đã được khai báo (MISA, Odoo, SAP, "
        "ERP nội bộ...) kèm các báo cáo mỗi nguồn cung cấp. Gọi tool này TRƯỚC khi "
        "muốn lấy báo cáo, để biết chính xác tên nguồn và tên báo cáo."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Lọc theo nhóm: 'connector', 'reporting', 'analytics'. Bỏ trống = tất cả.",
            }
        },
        "required": [],
    },
)
async def list_data_sources(category: Optional[str] = None) -> Dict[str, Any]:
    """
    Tool: xem công ty đã kết nối những nguồn dữ liệu nào.

    Dùng khi user hỏi: "công ty mình đang kết nối app nào?", "có báo cáo doanh
    thu ở đâu không?".
    """
    try:
        from mateai.infrastructure.connectors import custom_registry
        from mateai.infrastructure.connectors.operations import summarise

        wanted = (category or "").strip().lower() or None
        sources = custom_registry.list_sources(include_secrets=False)
        if wanted:
            sources = [s for s in sources if s.get("category") == wanted]

        items: List[Dict[str, Any]] = []
        for s in sources:
            items.append({
                "id": s.get("id"),
                "title": s.get("title"),
                "description": s.get("description") or "",
                "category": s.get("category"),
                # KHÔNG trả base_url cho AI: nó không cần biết hệ thống của
                # khách nằm ở đâu, và URL nội bộ trong ngữ cảnh là thông tin
                # không cần thiết phải lộ.
                "has_auth": bool(s.get("has_auth")),
                "enabled": bool(s.get("enabled", True)),
                "available_reports": s.get("available_paths") or [],
                "default_report": s.get("default_path") or "/",
                "kind": s.get("kind") or "rest",
            })
            full = custom_registry.get_source(s.get("id"), include_secrets=False) or {}
            if full.get("queries"):
                items[-1]["queries"] = summarise(full["queries"])      # tên + tham số: AI biết phải truyền gì
            if full.get("actions"):
                items[-1]["actions"] = summarise(full["actions"])      # thao tác can thiệp (cần duyệt)

        return {
            "success": True,
            "count": len(items),
            "data_sources": items,
            "hint": (
                "Dùng fetch_data_source với source_id và report để lấy dữ liệu. "
                "Nếu danh sách rỗng thì chưa có nguồn nào được khai báo — cần người "
                "dùng thêm trong tab Tích Hợp → Kết Nối."
            ) if not items else None,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase63] list_data_sources lỗi")
        return {"success": False, "error": str(e)}


@export_skill(
    name="fetch_data_source",
    description=(
        "Lấy dữ liệu báo cáo từ một nguồn đã khai báo (ERP, kế toán, CRM...). "
        "Dùng để trả lời câu hỏi cần số liệu, ví dụ 'doanh thu tháng này bao nhiêu'.\n\n"
        "CÁCH TRẢ LỜI — BẮT BUỘC: khi kết quả có `rows`, hãy trình bày dữ liệu thành BẢNG "
        "MARKDOWN (mỗi dòng `| giá trị | giá trị |`, có dòng `| --- | --- |` sau tiêu đề "
        "cột) chứ KHÔNG liệt kê dòng dạng văn bản hay dán JSON thô. Đặt tên cột bằng chính "
        "tên trong `columns` (tiếng Việt nếu trường có tiếng Việt), và luôn kèm một dòng "
        "tóm tắt con số quan trọng ra ngoài bảng. Nếu `truncated` là true, phải nói rõ "
        "bảng chỉ hiện một phần và còn bao nhiêu dòng nữa — không được im lặng cho người "
        "dùng tưởng đã thấy hết."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "Mã nguồn dữ liệu (ví dụ: 'misa-amh'). Xem qua list_data_sources.",
            },
            "report": {
                "type": "string",
                "description": "Tên báo cáo đã khai báo (ví dụ: 'doanh thu'). Bỏ trống = dùng báo cáo mặc định.",
            },
            "limit": {
                "type": "integer",
                "description": f"Số dòng tối đa cần xem. Mặc định {AI_ROW_LIMIT}, tối đa {AI_ROW_LIMIT}.",
            },
            "args": {
                "type": "object",
                "description": "Tham số của truy vấn đã khai báo (xem `queries[].params` trong list_data_sources), ví dụ {\"host\": \"srv01\"}.",
            },
        },
        "required": ["source_id"],
    },
)
async def fetch_data_source(
    source_id: str,
    report: Optional[str] = None,
    limit: int = AI_ROW_LIMIT,
    args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Tool: lấy dữ liệu báo cáo từ nguồn tùy chỉnh.

    Dùng khi user hỏi: "doanh thu tháng 3 bao nhiêu?", "tồn kho hiện tại ra sao?".
    """
    from mateai.infrastructure.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=False)
    if not source:
        return {
            "success": False,
            "error": f"Không có nguồn dữ liệu '{source_id}'",
            "hint": "Gọi list_data_sources để xem các nguồn đang có.",
        }

    rows_cap = max(1, min(AI_ROW_LIMIT, int(limit or AI_ROW_LIMIT)))
    params: Dict[str, Any] = {"limit": rows_cap}
    if report:
        params["path"] = report
    if isinstance(args, dict) and args:
        params["args"] = args

    async def _executor() -> Dict[str, Any]:
        from mateai.infrastructure.connectors import fetch_data_source as _fetch
        result = await _fetch(source_id, params)
        if not result.success:
            return {"success": False, "error": result.error}
        data = result.data or {}
        return {
            "success": True,
            "source": source.get("title"),
            "report": report or source.get("default_path"),
            "columns": data.get("columns", []),
            "rows": data.get("rows", []),
            "returned": data.get("returned", 0),
            "total": data.get("total", 0),
            "truncated": bool(data.get("truncated")),
            # Nhắc thẳng cho LLM, không chỉ trong mô tả tool: kết quả tool là
            # thứ nó đọc được ngay lúc đó, nên nhắc ở đây mới có tác dụng.
            "_format_hint": (
                "Trình bày `rows` thành bảng Markdown. Nếu truncated=true, phải nói rõ "
                "còn dòng chưa hiện và gợi ý dùng prepare_data_source_export để lấy đủ."
            ),
        }

    return await _run_with_hitl(
        action_name="data_source_fetch",
        params={"source_id": source_id, "report": report, "limit": rows_cap, "args": args or {}},
        executor=_executor,
        description=(
            f"AI Ly Ly yêu cầu báo cáo '{report or source.get('default_path')}' "
            f"từ nguồn {source.get('title')}"
        ),
    )


@export_skill(
    name="prepare_data_source_export",
    description=(
        "Dựng file báo cáo để tải xuống (Excel .xlsx hoặc CSV) và đưa vào hàng đợi "
        "tải. Dùng khi người dùng nói 'xuất ra Excel', 'gửi tôi file CSV', 'tải báo cáo "
        "tồn kho cho tôi'.\n\n"
        "CÁCH TRẢ LỜI — BẮT BUỘC: file được dựng ngay và giao diện sẽ TỰ ĐỘNG tải về máy "
        "người dùng, họ không cần bấm gì. Vì vậy bạn chỉ cần nói ngắn gọn: đã xuất bao "
        "nhiêu dòng, định dạng gì, tên file là gì, và đường dẫn file lưu ở đâu trên máy "
        "họ. TUYỆT ĐỐI KHÔNG tự dựng lại bảng dữ liệu trong câu trả lời — file đã đầy "
        "đủ hơn phần bạn xem trước đó, in ra lại chỉ làm rối."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "Mã nguồn dữ liệu. Xem qua list_data_sources.",
            },
            "report": {
                "type": "string",
                "description": "Tên báo cáo đã khai báo. Bỏ trống = dùng báo cáo mặc định.",
            },
            "format": {
                "type": "string",
                "enum": ["xlsx", "csv"],
                "description": "Định dạng file. Mặc định 'xlsx'.",
            },
            "max_rows": {
                "type": "integer",
                "description": "Số dòng tối đa. Mặc định 1000, tối đa 10000.",
            },
        },
        "required": ["source_id"],
    },
)
async def prepare_data_source_export(
    source_id: str,
    report: Optional[str] = None,
    format: str = "xlsx",
    max_rows: int = 1000,
) -> Dict[str, Any]:
    """
    Tool: dựng file báo cáo và bàn giao cho giao diện tải tự động.

    Dựng file NGAY TẠI ĐÂY thay vì trả một URL cho người dùng mở. Ba lý do:

      - URL có token thì bấm vào bằng trình duyệt vẫn 401 (đã kiểm chứng).
      - Câu trả lời của bạn được lưu vào lịch sử hội thoại rồi gửi lại cho nhà
        cung cấp LLM ở lượt sau — URL có token nằm trong đó là rò bí mật ra
        ngoài, dù token có hạn.
      - Người dùng phải bấm chuột thì vẫn không đúng nghĩa "ra lệnh là xong".

    Thay bằng `job_id` — mã ngẫu nhiên, không phải bí mật, vô dụng nếu không có
    phiên đăng nhập. Giao diện thấy job trong hàng đợi là tự tải về máy.
    """
    from mateai.infrastructure.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=False)
    if not source:
        return {
            "success": False,
            "error": f"Không có nguồn dữ liệu '{source_id}'",
            "hint": "Gọi list_data_sources để xem các nguồn đang có.",
        }

    fmt = (format or "xlsx").strip().lower()
    if fmt not in ("xlsx", "csv"):
        return {"success": False, "error": "format chỉ nhận 'xlsx' hoặc 'csv'"}

    rows_cap = max(1, min(10000, int(max_rows or 1000)))
    report_name = report or source.get("default_path") or "/"
    title = source.get("title") or source_id

    async def _executor() -> Dict[str, Any]:
        from mateai.infrastructure.connectors.generic_connector import GenericConnector
        from mateai.infrastructure.files.file_export import (
            _content_disposition,
            _rows_to_csv,
            _rows_to_xlsx,
            _safe_filename,
        )
        from mateai.infrastructure.files.download_queue import download_queue
        from datetime import datetime

        record = custom_registry.get_source(source_id, include_secrets=True)
        if not record:
            return {"success": False, "error": f"Không tìm thấy nguồn '{source_id}'"}

        # Gọi trực tiếp connector, không qua HTTP: cùng một dữ liệu nhưng không
        # cần token, không cần vòng gọi mạng về chính máy đang chạy.
        params: Dict[str, Any] = {"limit": rows_cap}
        if report:
            params["path"] = report
        result = await GenericConnector(record).fetch_data(params)

        if not result.success:
            return {"success": False, "error": result.error}

        data = result.data or {}
        data_rows = data.get("rows") or []
        columns = data.get("columns") or (list(data_rows[0].keys()) if data_rows else [])
        if not data_rows:
            return {"success": False, "error": "Nguồn dữ liệu không có bản ghi nào để xuất"}

        payload = (
            _rows_to_csv(data_rows, columns)
            if fmt == "csv"
            else _rows_to_xlsx(data_rows, columns, _safe_filename(title))
        )
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
        filename = f"{_safe_filename(title)}-{stamp}.{fmt}"
        disposition = _content_disposition(title, stamp, fmt)

        job = download_queue.put(
            source_id=source_id,
            source_title=title,
            report=report_name,
            fmt=fmt,
            title=title,
            filename=filename,
            payload=payload,
            row_count=len(data_rows),
        )

        return {
            "success": True,
            "job_id": job.job_id,
            "filename": job.filename,
            "format": fmt,
            "rows": len(data_rows),
            "total_available": data.get("total", len(data_rows)),
            "expires_in": 900,
            "content_disposition": disposition,
        }

    return await _run_with_hitl(
        action_name="data_source_export",
        params={"source_id": source_id, "report": report, "format": fmt, "max_rows": rows_cap},
        executor=_executor,
        description=f"AI Ly Ly dựng file {fmt.upper()} báo cáo '{report_name}' từ nguồn {title}",
    )


@export_skill(
    name="run_data_source_action",
    description=(
        "CAN THIỆP vào hệ thống doanh nghiệp qua một thao tác đã khai báo (ví dụ: tạo/đóng ticket, "
        "xác nhận cảnh báo, khởi động lại dịch vụ). Chỉ chạy được thao tác có trong `actions` của "
        "list_data_sources. LUÔN cần người có thẩm quyền duyệt; nếu kết quả có `awaiting_approval` "
        "thì nói với người dùng cần duyệt phiếu `approval_id` — KHÔNG tự coi là đã thực hiện."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "Mã nguồn dữ liệu."},
            "action": {"type": "string", "description": "Tên thao tác đã khai báo."},
            "params": {"type": "object", "description": "Tham số của thao tác (theo `actions[].params`)."},
        },
        "required": ["source_id", "action"],
    },
)
async def run_data_source_action(
    source_id: str,
    action: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tool ghi: mức rủi ro lấy từ khai báo của thao tác (tối thiểu 3 → bắt buộc duyệt)."""
    from mateai.infrastructure.connectors import custom_registry
    from mateai.infrastructure.connectors.generic_connector import run_source_action

    source = custom_registry.get_source(source_id, include_secrets=False)
    if not source:
        return {"success": False, "error": f"Không có nguồn dữ liệu '{source_id}'",
                "hint": "Gọi list_data_sources để xem các nguồn đang có."}
    decl = (source.get("actions") or {}).get(action)
    if not decl:
        return {"success": False, "error": f"Nguồn '{source_id}' không có thao tác '{action}'",
                "available_actions": sorted((source.get("actions") or {}).keys())}
    risk = max(3, int(decl.get("risk_level") or 3))
    args = params if isinstance(params, dict) else {}

    async def _executor() -> Dict[str, Any]:
        result = await run_source_action(source_id, action, args)
        if not result.success:
            return {"success": False, "error": result.error}
        return {"success": True, "source": source.get("title"), "action": action, "result": result.data}

    from mateai.application.security.zero_trust import execute_with_hitl

    envelope = await execute_with_hitl(
        action_name="data_source_action",
        params={"source_id": source_id, "action": action, "params": args},
        executor=_executor,
        requested_by="AI_Agent",
        agent_id="VN-MATEAI-CONNECTOR",
        check_rbac=False,
        description=f"AI Ly Ly can thiệp '{action}' trên {source.get('title')}: {decl.get('description') or ''}".strip(),
        risk_level=risk,
    )
    if envelope.get("status") == "denied":
        return {"success": False, "error": envelope.get("message"), "policy_denied": True}
    if envelope.get("status") == "awaiting_approval":
        return {"success": False, "awaiting_approval": True, "approval_id": envelope.get("approval_id"),
                "risk_level": envelope.get("risk_level"),
                "message": f"{envelope.get('message', 'Cần duyệt.')} Báo người dùng duyệt phiếu trong hàng đợi HITL."}
    return envelope.get("result") or {"success": False, "error": "Cổng HITL không trả về kết quả"}
