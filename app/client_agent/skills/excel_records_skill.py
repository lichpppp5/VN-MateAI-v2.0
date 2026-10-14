# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/excel_records_skill.py
==============================
Seed Skill 2 — Genealogy / Hán Nôm Record Entry via Excel COM Interface.

Provides:
  - append_genealogy_record: Open an Excel workbook in headless COM mode
    (Visible=False), locate the next empty row, write structured genealogy
    fields, and save the file — without any GUI interaction.

Requirements on host:
  - Microsoft Excel must be installed (any version supporting COM automation).
  - pywin32 package must be installed (pip install pywin32).

Field mapping (columns A–G):
  A: STT (Auto-incremented row index)
  B: Họ và tên (Full name)
  C: Đời thứ (Generation number)
  D: Chi phái (Branch / lineage)
  E: Năm sinh (Birth year)
  F: Năm mất (Death year)
  G: Ghi chú / Chữ Húy (Notes / posthumous name in Hán Nôm)
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# COM automation helper
# ---------------------------------------------------------------------------


def _get_excel_com() -> Any:
    """
    Lazily import and return a win32com Excel.Application COM object.
    Raises ImportError on non-Windows or missing pywin32.
    """
    try:
        import win32com.client  # type: ignore[import]
        excel = win32com.client.Dispatch("Excel.Application")
        excel.Visible = False          # Run completely headless
        excel.DisplayAlerts = False    # Suppress save dialogs
        excel.ScreenUpdating = False   # Speed up writes
        return excel
    except ImportError as exc:
        raise ImportError(
            "pywin32 is required for Excel COM automation. "
            "Install it with: pip install pywin32"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to initialise Excel COM: {exc}") from exc


# ---------------------------------------------------------------------------
# Skill: Append a genealogy record to an Excel workbook
# ---------------------------------------------------------------------------


@export_skill(
    name="append_genealogy_record",
    description=(
        "Nhập liệu gia phả / Hán Nôm vào file Excel thông qua COM Interface. "
        "Tự động tìm dòng trống tiếp theo và ghi các trường: "
        "Họ tên, Đời thứ, Chi phái, Năm sinh, Năm mất, Ghi chú chữ Húy."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "excel_path": {
                "type": "string",
                "description": "Đường dẫn tuyệt đối đến file Excel (.xlsx hoặc .xls).",
            },
            "ho_ten": {
                "type": "string",
                "description": "Họ và tên đầy đủ (có thể bao gồm chữ Hán Nôm).",
            },
            "doi_thu": {
                "type": "integer",
                "description": "Đời thứ trong gia phả (số nguyên, ví dụ: 5).",
            },
            "chi_phai": {
                "type": "string",
                "description": "Chi phái / nhánh gia tộc (ví dụ: 'Chi trưởng', 'Chi thứ hai').",
                "default": "",
            },
            "nam_sinh": {
                "type": "integer",
                "description": "Năm sinh (dương lịch). Dùng 0 nếu không rõ.",
                "default": 0,
            },
            "nam_mat": {
                "type": "integer",
                "description": "Năm mất (dương lịch). Dùng 0 nếu còn sống hoặc không rõ.",
                "default": 0,
            },
            "ghi_chu": {
                "type": "string",
                "description": "Ghi chú bổ sung, bao gồm chữ Húy hoặc hiệu (Hán Nôm).",
                "default": "",
            },
            "sheet_name": {
                "type": "string",
                "description": "Tên sheet cần ghi vào. Mặc định là sheet đầu tiên.",
                "default": "",
            },
        },
        "required": ["excel_path", "ho_ten", "doi_thu"],
    },
)
def append_genealogy_record(
    excel_path: str,
    ho_ten: str,
    doi_thu: int,
    chi_phai: str = "",
    nam_sinh: int = 0,
    nam_mat: int = 0,
    ghi_chu: str = "",
    sheet_name: str = "",
) -> Dict[str, Any]:
    """
    Append a single genealogy record to an Excel file via COM automation.

    Args:
        excel_path: Absolute path to the target .xlsx/.xls file.
        ho_ten:     Full name (supports Unicode / Hán Nôm characters).
        doi_thu:    Generation number (integer).
        chi_phai:   Lineage branch label.
        nam_sinh:   Birth year (0 = unknown).
        nam_mat:    Death year (0 = alive / unknown).
        ghi_chu:    Notes / posthumous honorific name.
        sheet_name: Target worksheet name (empty = first sheet).

    Returns:
        {"success": bool, "row_written": int, "file": str, "error": Optional[str]}
    """
    excel_path_obj = Path(excel_path)
    if not excel_path_obj.exists():
        return {
            "success": False,
            "row_written": -1,
            "file": excel_path,
            "error": f"File không tồn tại: {excel_path}",
        }

    excel = None
    workbook = None
    try:
        excel = _get_excel_com()
        workbook = excel.Workbooks.Open(str(excel_path_obj.resolve()))

        # Select the target sheet
        if sheet_name:
            try:
                sheet = workbook.Sheets(sheet_name)
            except Exception:
                return {
                    "success": False,
                    "row_written": -1,
                    "file": excel_path,
                    "error": f"Sheet '{sheet_name}' không tồn tại trong workbook.",
                }
        else:
            sheet = workbook.Sheets(1)

        # Find the next empty row by scanning column A (STT column)
        last_used_row: int = sheet.UsedRange.Rows.Count
        # Walk down from last used row to find actual last non-empty cell in col A
        next_row: int = last_used_row + 1
        for row_idx in range(last_used_row, 0, -1):
            cell_val = sheet.Cells(row_idx, 1).Value
            if cell_val is not None and str(cell_val).strip():
                next_row = row_idx + 1
                break
        else:
            next_row = 2  # Assume row 1 is header

        # Auto-increment STT based on row position
        stt: int = next_row - 1  # Row 2 → STT=1, Row 3 → STT=2, etc.

        # Write data into columns A–G
        sheet.Cells(next_row, 1).Value = stt
        sheet.Cells(next_row, 2).Value = ho_ten
        sheet.Cells(next_row, 3).Value = doi_thu
        sheet.Cells(next_row, 4).Value = chi_phai
        sheet.Cells(next_row, 5).Value = nam_sinh if nam_sinh != 0 else ""
        sheet.Cells(next_row, 6).Value = nam_mat if nam_mat != 0 else ""
        sheet.Cells(next_row, 7).Value = ghi_chu

        workbook.Save()
        logger.info(
            "Genealogy record written: row=%d, name='%s', gen=%d, file=%s",
            next_row, ho_ten, doi_thu, excel_path,
        )
        return {
            "success": True,
            "row_written": next_row,
            "stt": stt,
            "file": excel_path,
            "error": None,
        }

    except Exception:  # pylint: disable=broad-except
        error_detail = traceback.format_exc()
        logger.error("Excel COM error:\n%s", error_detail)
        return {
            "success": False,
            "row_written": -1,
            "file": excel_path,
            "error": error_detail,
        }
    finally:
        # Always close workbook and quit COM to release file lock
        if workbook is not None:
            try:
                workbook.Close(SaveChanges=False)
            except Exception:  # pylint: disable=broad-except
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:  # pylint: disable=broad-except
                pass
        # Release COM reference
        try:
            import pythoncom  # type: ignore[import]
            pythoncom.CoUninitialize()
        except Exception:  # pylint: disable=broad-except
            pass
