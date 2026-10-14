# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/skills/file_system.py
==========================
Phase 38: Native File System & OS Toolkit (OpenClaw Parity).

Cung cấp bộ kỹ năng thao tác tệp tin (File I/O) gốc bằng Python:
  - list_directory: Liệt kê nội dung thư mục (tên file, dung lượng, ngày sửa).
  - read_file: Đọc nội dung file text/log (tự động đọc N dòng cuối nếu file quá lớn).
  - write_file: Tạo file mới, ghi đè hoặc ghi nối tiếp (tự động tạo thư mục cha).
  - delete_item: Xóa tệp tin đơn lẻ hoặc xóa thư mục.

Mọi thao tác đều có try/except chặt chẽ trả về thông báo lỗi chi tiết cho LLM.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Base project root for relative path resolution
# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)

# Robust export_skill decorator import (compatible with Master and Client Agents)
try:
    from core.plugin_manager import export_skill
except Exception:
    try:
        from client_agent.core.plugin_manager import export_skill
    except Exception:
        def export_skill(*args, **kwargs):
            def decorator(fn):
                return fn
            return decorator


def _resolve_path(raw_path: str) -> Path:
    """Resolve raw string path into an absolute Path object, expanding home directory if present."""
    p = Path(raw_path.strip()).expanduser()
    if not p.is_absolute():
        p = (_PROJECT_ROOT / p).resolve()
    return p


# Tệp chứa bí mật (khoá LLM, token Telegram, khoá ký JWT/thiết bị, khoá TLS,
# CSDL có hash mật khẩu). `read_file` là tool rủi ro thấp, chạy không cần
# duyệt — nếu đọc được các tệp này thì bất kỳ ai ra lệnh được cho agent đều
# lấy được toàn bộ bí mật của máy chủ.
_SECRET_FILE_NAMES = {"config.json", "credentials.json", "token.json"}
_SECRET_SUFFIXES = {".key", ".pem", ".crt", ".p12", ".pfx", ".kdbx", ".db", ".sqlite", ".sqlite3"}


def _is_secret_file(p: Path) -> bool:
    name = p.name.lower()
    if name in _SECRET_FILE_NAMES or name == ".env" or name.startswith(".env."):
        return True
    if p.suffix.lower() in _SECRET_SUFFIXES:
        return True
    try:
        return p.resolve().is_relative_to((_PROJECT_ROOT / "data").resolve()) and p.suffix.lower() in {".key", ".json"}
    except (OSError, ValueError):
        return False


#: Thư mục con của dự án mà tool được GHI / XOÁ (kết quả, báo cáo, dữ liệu tạm).
#: Mọi chỗ khác trong dự án — mã nguồn, cấu hình, `identity_core.md` (được nạp vào
#: chỉ thị hệ thống của AI), chứng chỉ, CSDL — chỉ đọc với tool (prompt §39, §94).
_WRITABLE_PROJECT_DIRS = ("reports", "storage", "exports")


def _write_denied_reason(p: Path) -> Optional[str]:
    """Lý do cấm ghi / xoá `p` bằng tool, hoặc None nếu được phép."""
    try:
        target = p.resolve()
    except OSError:
        return "Không xác định được đường dẫn."
    if _is_secret_file(target):
        return "Tệp chứa bí mật — tool không được ghi / xoá."
    root = _PROJECT_ROOT.resolve()
    if target == root or target.is_relative_to(root):
        rel = target.relative_to(root).parts
        if not rel or rel[0] not in _WRITABLE_PROJECT_DIRS:
            return (f"Trong thư mục dự án, tool chỉ được ghi / xoá trong {', '.join(_WRITABLE_PROJECT_DIRS)}/ "
                    "(mã nguồn, cấu hình và chỉ thị hệ thống được bảo vệ).")
        return None
    try:
        from mateai.config.loader import settings
        protected = [Path(d) for d in (settings.security.protected_directories or [])]
    except Exception:  # noqa: BLE001
        protected = []
    for d in protected:
        try:
            if target.is_relative_to(d.resolve()):
                return f"Thư mục hệ thống được bảo vệ: '{d}'."
        except (OSError, ValueError):
            continue
    return None


def _format_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# 1. list_directory
# ---------------------------------------------------------------------------

@export_skill(
    name="list_directory",
    description="Liệt kê nội dung thư mục bao gồm tên file/thư mục con, dung lượng và thời gian sửa đổi gần nhất.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Đường dẫn thư mục cần liệt kê (ví dụ: '.', './logs', 'C:\\Logs', '/var/log').",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm đích cần thực thi (mặc định: 'master').",
            },
            "target_client_id": {
                "type": "string",
                "description": "Bí danh thay thế cho target_client nếu có.",
            },
        },
        "required": ["path"],
    },
)
def list_directory(path: str = ".", **kwargs: Any) -> Dict[str, Any]:
    """
    Liệt kê nội dung thư mục (tên file, dung lượng, ngày sửa).

    Args:
        path: Đường dẫn thư mục.

    Returns:
        Dict chứa trạng thái và danh sách các mục bên trong thư mục.
    """
    try:
        target_path = _resolve_path(path)

        if not target_path.exists():
            return {
                "status": "error",
                "error": f"Thư mục không tồn tại: '{path}' (đường dẫn tuyệt đối: {target_path})",
                "path": str(target_path),
            }

        if not target_path.is_dir():
            return {
                "status": "error",
                "error": f"Đường dẫn đã cho là tệp tin, không phải thư mục: '{path}'",
                "path": str(target_path),
            }

        items: List[Dict[str, Any]] = []
        try:
            with os.scandir(target_path) as it:
                for entry in it:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        is_directory = entry.is_dir(follow_symlinks=False)
                        size_bytes = 0 if is_directory else stat.st_size

                        items.append({
                            "name": entry.name,
                            "type": "directory" if is_directory else "file",
                            "size_bytes": size_bytes,
                            "size_human": "-" if is_directory else _format_size(size_bytes),
                            "modified_at": mod_time,
                        })
                    except (PermissionError, FileNotFoundError):
                        items.append({
                            "name": entry.name,
                            "type": "unknown",
                            "size_bytes": 0,
                            "size_human": "-",
                            "modified_at": "unknown",
                        })
        except PermissionError:
            return {
                "status": "error",
                "error": f"Không có quyền truy cập thư mục: '{path}' (Permission Denied).",
                "path": str(target_path),
            }

        # Sắp xếp: thư mục lên trước, sau đó theo tên A-Z
        items.sort(key=lambda x: (0 if x["type"] == "directory" else 1, x["name"].lower()))

        return {
            "status": "success",
            "path": str(target_path),
            "total_items": len(items),
            "items": items,
        }

    except Exception as exc:
        logger.error("[FileSystem] Lỗi khi liệt kê thư mục '%s': %s", path, exc, exc_info=True)
        return {
            "status": "error",
            "error": f"Lỗi không xác định khi liệt kê thư mục '{path}': {str(exc)}",
        }


# ---------------------------------------------------------------------------
# 2. read_file
# ---------------------------------------------------------------------------

@export_skill(
    name="read_file",
    description="Đọc nội dung tệp tin văn bản hoặc file log hệ thống. Tự động đọc N dòng cuối nếu file quá lớn để tránh tràn bộ nhớ.",
    parameters_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Đường dẫn tệp tin cần đọc (ví dụ: 'logs/run.log', 'config.json').",
            },
            "lines": {
                "type": "integer",
                "description": "Số lượng dòng tối đa cần đọc. Mặc định là 500 dòng cuối nếu file lớn.",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm đích cần thực thi (mặc định: 'master').",
            },
            "target_client_id": {
                "type": "string",
                "description": "Bí danh thay thế cho target_client nếu có.",
            },
        },
        "required": ["file_path"],
    },
)
def read_file(file_path: str, lines: int = 500, **kwargs: Any) -> Dict[str, Any]:
    """
    Đọc nội dung file text/log. Nếu file quá lớn (như file log server),
    chỉ đọc `lines` dòng cuối cùng để tránh tràn context window của 9router.

    Args:
        file_path: Đường dẫn tới file.
        lines: Số dòng tối đa lấy từ cuối file (mặc định 500).

    Returns:
        Dict chứa trạng thái, số dòng, nội dung văn bản và cờ truncated.
    """
    try:
        target_path = _resolve_path(file_path)

        if not target_path.exists():
            return {
                "status": "error",
                "error": f"Tệp tin không tồn tại: '{file_path}' (đường dẫn tuyệt đối: {target_path})",
                "file_path": str(target_path),
            }

        if target_path.is_dir():
            return {
                "status": "error",
                "error": f"Đường dẫn đã cho là thư mục, không thể đọc dưới dạng tệp tin: '{file_path}'",
                "file_path": str(target_path),
            }

        if _is_secret_file(target_path.resolve()):
            logger.warning("[FileSystem] Từ chối đọc tệp chứa bí mật: %s", target_path)
            return {
                "status": "error",
                "error": f"Tệp '{target_path.name}' chứa bí mật hệ thống, không được đọc qua công cụ này.",
                "file_path": str(target_path),
            }

        max_lines = max(1, int(lines or 500))

        # Đọc file với cơ chế đa encoding (UTF-8 ưu tiên, fallback latin-1/replace)
        raw_text: str = ""
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except PermissionError:
            return {
                "status": "error",
                "error": f"Không có quyền đọc tệp tin: '{file_path}' (Permission Denied).",
                "file_path": str(target_path),
            }

        total_lines = len(all_lines)
        is_truncated = False

        if total_lines > max_lines:
            selected_lines = all_lines[-max_lines:]
            is_truncated = True
            header_note = (
                f"[THÔNG BÁO HỆ THỐNG: File '{target_path.name}' có tổng cộng {total_lines} dòng. "
                f"Chỉ hiển thị {max_lines} dòng cuối cùng để bảo toàn context window.]\n\n"
            )
            content = header_note + "".join(selected_lines)
        else:
            selected_lines = all_lines
            content = "".join(selected_lines)

        file_stat = target_path.stat()

        return {
            "status": "success",
            "file_path": str(target_path),
            "filename": target_path.name,
            "total_lines": total_lines,
            "lines_returned": len(selected_lines),
            "truncated": is_truncated,
            "size_bytes": file_stat.st_size,
            "size_human": _format_size(file_stat.st_size),
            "content": content,
        }

    except Exception as exc:
        logger.error("[FileSystem] Lỗi khi đọc file '%s': %s", file_path, exc, exc_info=True)
        return {
            "status": "error",
            "error": f"Lỗi không xác định khi đọc tệp tin '{file_path}': {str(exc)}",
        }


# ---------------------------------------------------------------------------
# 3. write_file
# ---------------------------------------------------------------------------

@export_skill(
    name="write_file",
    description="Tạo file mới hoặc ghi đè (mode='w'), hoặc ghi tiếp vào cuối file (mode='a'). Hỗ trợ tự động tạo thư mục cha.",
    parameters_schema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Đường dẫn tệp tin cần tạo mới hoặc ghi nội dung (ví dụ: 'scripts/clean_cache.py', 'reports/test.txt').",
            },
            "content": {
                "type": "string",
                "description": "Nội dung văn bản cần ghi vào tệp tin.",
            },
            "mode": {
                "type": "string",
                "enum": ["w", "a"],
                "description": "Chế độ ghi: 'w' (tạo mới hoặc ghi đè toàn bộ), 'a' (ghi nối tiếp vào cuối file). Mặc định: 'w'.",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm đích cần thực thi (mặc định: 'master').",
            },
            "target_client_id": {
                "type": "string",
                "description": "Bí danh thay thế cho target_client nếu có.",
            },
        },
        "required": ["file_path", "content"],
    },
)
def write_file(file_path: str, content: str, mode: str = "w", **kwargs: Any) -> Dict[str, Any]:
    """
    Tạo file mới hoặc ghi đè (mode="w"), hoặc ghi tiếp vào cuối file (mode="a").
    Hỗ trợ tự động tạo thư mục cha nếu chưa tồn tại.

    Args:
        file_path: Đường dẫn tệp tin.
        content: Nội dung chuỗi cần ghi.
        mode: "w" (ghi mới/ghi đè) hoặc "a" (ghi tiếp). Mặc định "w".

    Returns:
        Dict kết quả ghi file.
    """
    try:
        clean_mode = str(mode or "w").strip().lower()
        if clean_mode not in ("w", "a"):
            return {
                "status": "error",
                "error": f"Chế độ ghi không hợp lệ: '{mode}'. Chỉ chấp nhận 'w' (ghi đè) hoặc 'a' (ghi nối tiếp).",
                "file_path": file_path,
            }

        target_path = _resolve_path(file_path)
        denied = _write_denied_reason(target_path)
        if denied:
            return {"status": "error", "error": f"Từ chối ghi '{target_path}': {denied}", "file_path": str(target_path)}

        # Tự động tạo thư mục cha nếu chưa có
        parent_dir = target_path.parent
        if not parent_dir.exists():
            try:
                parent_dir.mkdir(parents=True, exist_ok=True)
                logger.info("[FileSystem] Đã tự động tạo thư mục cha: %s", parent_dir)
            except PermissionError:
                return {
                    "status": "error",
                    "error": f"Không có quyền tạo thư mục cha: '{parent_dir}' (Permission Denied).",
                    "file_path": str(target_path),
                }

        # Ghi file
        try:
            with open(target_path, clean_mode, encoding="utf-8") as f:
                bytes_written = f.write(content)
        except PermissionError:
            return {
                "status": "error",
                "error": f"Không có quyền ghi vào tệp tin: '{file_path}' (Permission Denied).",
                "file_path": str(target_path),
            }

        file_stat = target_path.stat()
        mode_desc = "ghi đè / tạo mới" if clean_mode == "w" else "ghi nối tiếp"

        logger.info("[FileSystem] Đã %s thành công file '%s' (%d bytes ghi mới)", mode_desc, target_path, bytes_written)

        return {
            "status": "success",
            "message": f"Đã {mode_desc} tệp tin '{target_path.name}' thành công ({bytes_written} ký tự).",
            "file_path": str(target_path),
            "filename": target_path.name,
            "mode": clean_mode,
            "bytes_written": bytes_written,
            "total_size_bytes": file_stat.st_size,
            "total_size_human": _format_size(file_stat.st_size),
        }

    except Exception as exc:
        logger.error("[FileSystem] Lỗi khi ghi file '%s': %s", file_path, exc, exc_info=True)
        return {
            "status": "error",
            "error": f"Lỗi không xác định khi ghi tệp tin '{file_path}': {str(exc)}",
        }


# ---------------------------------------------------------------------------
# 4. delete_item
# ---------------------------------------------------------------------------

@export_skill(
    name="delete_item",
    description="Xóa tệp tin đơn lẻ hoặc thư mục trên hệ thống.",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Đường dẫn tệp tin hoặc thư mục cần xóa.",
            },
            "is_folder": {
                "type": "boolean",
                "description": "Đặt thành true nếu đối tượng cần xóa là thư mục. Mặc định là false (xóa tệp tin đơn lẻ).",
            },
            "target_client": {
                "type": "string",
                "description": "ID hoặc tên máy trạm đích cần thực thi (mặc định: 'master').",
            },
            "target_client_id": {
                "type": "string",
                "description": "Bí danh thay thế cho target_client nếu có.",
            },
        },
        "required": ["path"],
    },
)
def delete_item(path: str, is_folder: bool = False, **kwargs: Any) -> Dict[str, Any]:
    """
    Xóa file hoặc thư mục (sử dụng os.remove hoặc shutil.rmtree).

    Args:
        path: Đường dẫn file hoặc thư mục.
        is_folder: True nếu là thư mục, False nếu là file.

    Returns:
        Dict kết quả xóa.
    """
    try:
        target_path = _resolve_path(path)

        if not target_path.exists():
            return {
                "status": "error",
                "error": f"Đối tượng cần xóa không tồn tại: '{path}' (đường dẫn tuyệt đối: {target_path})",
                "path": str(target_path),
            }

        denied = _write_denied_reason(target_path)
        if denied:
            return {"status": "error", "error": f"Từ chối xoá '{target_path}': {denied}", "path": str(target_path)}

        # Bảo vệ các thư mục cốt lõi quan trọng (Safety Guard fallback)
        protected_roots = [Path("/").resolve(), Path("C:\\").resolve(), _PROJECT_ROOT.resolve()]
        if target_path.resolve() in protected_roots:
            return {
                "status": "error",
                "error": f"Từ chối xóa đường dẫn hệ thống gốc hoặc thư mục dự án cốt lõi: '{target_path}'",
                "path": str(target_path),
            }

        if is_folder:
            if not target_path.is_dir():
                return {
                    "status": "error",
                    "error": f"Tham số is_folder=True nhưng '{path}' là một tệp tin, không phải thư mục.",
                    "path": str(target_path),
                }

            try:
                shutil.rmtree(target_path)
                logger.info("[FileSystem] Đã xóa thư mục: %s", target_path)
                return {
                    "status": "success",
                    "message": f"Đã xóa hoàn toàn thư mục '{target_path.name}'.",
                    "path": str(target_path),
                    "is_folder": True,
                }
            except PermissionError:
                return {
                    "status": "error",
                    "error": f"Không có quyền xóa thư mục: '{path}' (Permission Denied).",
                    "path": str(target_path),
                }

        else:
            if target_path.is_dir():
                return {
                    "status": "error",
                    "error": f"'{path}' là một thư mục. Để xóa thư mục, vui lòng thiết lập tham số is_folder=True.",
                    "path": str(target_path),
                }

            try:
                os.remove(target_path)
                logger.info("[FileSystem] Đã xóa tệp tin: %s", target_path)
                return {
                    "status": "success",
                    "message": f"Đã xóa tệp tin '{target_path.name}'.",
                    "path": str(target_path),
                    "is_folder": False,
                }
            except PermissionError:
                return {
                    "status": "error",
                    "error": f"Không có quyền xóa tệp tin: '{path}' (Permission Denied).",
                    "path": str(target_path),
                }

    except Exception as exc:
        logger.error("[FileSystem] Lỗi khi xóa '%s': %s", path, exc, exc_info=True)
        return {
            "status": "error",
            "error": f"Lỗi không xác định khi xóa '{path}': {str(exc)}",
        }
