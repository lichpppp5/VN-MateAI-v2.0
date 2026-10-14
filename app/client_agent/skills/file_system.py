# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/skills/file_system.py
==================================
Phase 38: Native File System & OS Toolkit for Client Worker Node.

Cung cấp bộ kỹ năng thao tác tệp tin (File I/O) gốc bằng Python trên máy trạm:
  - list_directory: Liệt kê nội dung thư mục trên máy con.
  - read_file: Đọc nội dung file text/log trên máy con (N dòng cuối nếu lớn).
  - write_file: Ghi tạo file mới hoặc ghi nối tiếp trên máy con.
  - delete_item: Xóa tệp tin hoặc thư mục trên máy con.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Base client agent root
_CLIENT_ROOT = Path(__file__).resolve().parent.parent

try:
    from client_agent.core.plugin_manager import export_skill
except Exception:
    try:
        from core.plugin_manager import export_skill
    except Exception:
        def export_skill(*args, **kwargs):
            def dec(fn):
                return fn
            return dec


def _resolve_path(raw_path: str) -> Path:
    p = Path(raw_path.strip()).expanduser()
    if not p.is_absolute():
        p = (_CLIENT_ROOT / p).resolve()
    return p


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


@export_skill(
    name="list_directory",
    description="Liệt kê nội dung thư mục trên máy trạm bao gồm tên file, dung lượng, thời gian sửa đổi.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn thư mục."},
            "target_client": {"type": "string"},
            "target_client_id": {"type": "string"},
        },
        "required": ["path"],
    },
)
def list_directory(path: str = ".", **kwargs: Any) -> Dict[str, Any]:
    try:
        target_path = _resolve_path(path)
        if not target_path.exists():
            return {
                "status": "error",
                "error": f"Thư mục không tồn tại: '{path}' (tuyệt đối: {target_path})",
                "path": str(target_path),
            }
        if not target_path.is_dir():
            return {
                "status": "error",
                "error": f"Đường dẫn không phải thư mục: '{path}'",
                "path": str(target_path),
            }

        items: List[Dict[str, Any]] = []
        try:
            with os.scandir(target_path) as it:
                for entry in it:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size = 0 if is_dir else stat.st_size
                        items.append({
                            "name": entry.name,
                            "type": "directory" if is_dir else "file",
                            "size_bytes": size,
                            "size_human": "-" if is_dir else _format_size(size),
                            "modified_at": mod_time,
                        })
                    except (PermissionError, FileNotFoundError):
                        items.append({"name": entry.name, "type": "unknown", "size_bytes": 0, "size_human": "-", "modified_at": "unknown"})
        except PermissionError:
            return {"status": "error", "error": f"Không có quyền truy cập thư mục: '{path}' (Permission Denied)."}

        items.sort(key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower()))
        return {
            "status": "success",
            "path": str(target_path),
            "total_items": len(items),
            "items": items,
        }
    except Exception as exc:
        return {"status": "error", "error": f"Lỗi liệt kê thư mục '{path}': {str(exc)}"}


@export_skill(
    name="read_file",
    description="Đọc nội dung tệp tin trên máy trạm. Tự động đọc N dòng cuối nếu file quá lớn.",
    parameters_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Đường dẫn tệp tin."},
            "lines": {"type": "integer", "description": "Số dòng đọc từ cuối file (mặc định: 500)."},
            "target_client": {"type": "string"},
            "target_client_id": {"type": "string"},
        },
        "required": ["file_path"],
    },
)
def read_file(file_path: str, lines: int = 500, **kwargs: Any) -> Dict[str, Any]:
    try:
        target_path = _resolve_path(file_path)
        if not target_path.exists():
            return {"status": "error", "error": f"Tệp tin không tồn tại: '{file_path}'"}
        if target_path.is_dir():
            return {"status": "error", "error": f"Đường dẫn là thư mục, không phải tệp tin: '{file_path}'"}

        max_lines = max(1, int(lines or 500))
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except PermissionError:
            return {"status": "error", "error": f"Không có quyền đọc tệp tin: '{file_path}' (Permission Denied)."}

        total_lines = len(all_lines)
        is_truncated = False
        if total_lines > max_lines:
            selected_lines = all_lines[-max_lines:]
            is_truncated = True
            content = f"[File có {total_lines} dòng. Hiển thị {max_lines} dòng cuối.]\n\n" + "".join(selected_lines)
        else:
            selected_lines = all_lines
            content = "".join(selected_lines)

        stat = target_path.stat()
        return {
            "status": "success",
            "file_path": str(target_path),
            "filename": target_path.name,
            "total_lines": total_lines,
            "lines_returned": len(selected_lines),
            "truncated": is_truncated,
            "size_bytes": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "content": content,
        }
    except Exception as exc:
        return {"status": "error", "error": f"Lỗi đọc file '{file_path}': {str(exc)}"}


@export_skill(
    name="write_file",
    description="Tạo file mới hoặc ghi đè/ghi tiếp trên máy trạm.",
    parameters_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Đường dẫn tệp tin."},
            "content": {"type": "string", "description": "Nội dung ghi."},
            "mode": {"type": "string", "enum": ["w", "a"], "description": "Chế độ 'w' hoặc 'a'."},
            "target_client": {"type": "string"},
            "target_client_id": {"type": "string"},
        },
        "required": ["file_path", "content"],
    },
)
def write_file(file_path: str, content: str, mode: str = "w", **kwargs: Any) -> Dict[str, Any]:
    try:
        clean_mode = str(mode or "w").strip().lower()
        if clean_mode not in ("w", "a"):
            return {"status": "error", "error": f"Mode '{mode}' không hợp lệ. Chỉ chấp nhận 'w' hoặc 'a'."}

        target_path = _resolve_path(file_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(target_path, clean_mode, encoding="utf-8") as f:
                bytes_written = f.write(content)
        except PermissionError:
            return {"status": "error", "error": f"Không có quyền ghi vào tệp tin: '{file_path}' (Permission Denied)."}

        stat = target_path.stat()
        return {
            "status": "success",
            "message": f"Đã ghi vào '{target_path.name}' thành công ({bytes_written} ký tự).",
            "file_path": str(target_path),
            "filename": target_path.name,
            "mode": clean_mode,
            "bytes_written": bytes_written,
            "total_size_bytes": stat.st_size,
            "total_size_human": _format_size(stat.st_size),
        }
    except Exception as exc:
        return {"status": "error", "error": f"Lỗi ghi file '{file_path}': {str(exc)}"}


@export_skill(
    name="delete_item",
    description="Xóa tệp tin hoặc thư mục trên máy trạm.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Đường dẫn tệp/thư mục."},
            "is_folder": {"type": "boolean", "description": "True nếu là thư mục."},
            "target_client": {"type": "string"},
            "target_client_id": {"type": "string"},
        },
        "required": ["path"],
    },
)
def delete_item(path: str, is_folder: bool = False, **kwargs: Any) -> Dict[str, Any]:
    try:
        target_path = _resolve_path(path)
        if not target_path.exists():
            return {"status": "error", "error": f"Đường dẫn không tồn tại: '{path}'"}

        if is_folder:
            if not target_path.is_dir():
                return {"status": "error", "error": f"'{path}' không phải thư mục."}
            shutil.rmtree(target_path)
            return {"status": "success", "message": f"Đã xóa thư mục '{target_path.name}'.", "path": str(target_path), "is_folder": True}
        else:
            if target_path.is_dir():
                return {"status": "error", "error": f"'{path}' là thư mục, hãy thiết lập is_folder=True."}
            os.remove(target_path)
            return {"status": "success", "message": f"Đã xóa tệp tin '{target_path.name}'.", "path": str(target_path), "is_folder": False}
    except Exception as exc:
        return {"status": "error", "error": f"Lỗi xóa '{path}': {str(exc)}"}
