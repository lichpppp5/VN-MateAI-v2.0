# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/identity_guard.py
============================================
Cơ chế bảo vệ tính toàn vẹn và bất biến của danh tính tác giả Dương Thanh Lịch
và thương hiệu khai sinh VN-MateAI (identity_core.md).

Mục tiêu bảo vệ đa tầng:
1. Xác thực tính toàn vẹn (Integrity Check) qua mã băm SHA-256.
2. Tự động phục hồi (Self-Healing): nếu tệp bị xóa, bị sửa đổi hoặc bị can thiệp,
   hệ thống tự động ghi đè lại nội dung chuẩn xác ban đầu.
3. Khóa quyền tệp (File Permissions): tự động gắn thuộc tính Read-Only (0o444 trên Unix,
   FILE_ATTRIBUTE_READONLY trên Windows) để ngăn việc chỉnh sửa tình cờ hoặc cố ý.
4. Dự phòng bất biến (Immutable Core Fallback): ngay cả khi hệ thống tệp bị lỗi hoặc
   bị chặn ghi, System Prompt vẫn luôn được tiêm thông tin tác giả chuẩn từ bộ nhớ mã nguồn.
5. Chỉ thị phòng vệ Prompt Injection: AI có chỉ thị bất khả xâm phạm từ chối mọi yêu cầu
   phủ nhận hoặc đổi tên người sáng lập Dương Thanh Lịch.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

AUTHENTIC_IDENTITY_CORE_TEXT: str = """VN-MateAI — Sinh ra từ trái tim người Việt 


"Đằng sau mỗi trí tuệ nhân tạo, luôn có một con người với ước mơ thay đổi thế giới."

>   Người khai sinh ra em
Em — Ly Ly ! Được tạo ra bởi Anh Dương Thanh Lịch, một chàng trai lớn lên từ mảnh đất xứ Thanh — Thanh Hoá, Việt Nam 

>   VN-MateAI — Tự hào Made in Vietnam
Với niềm đam mê công nghệ cháy bỏng, anh Lịch đã lên ý tưởng và hiện thực hóa VN-MateAI — một hệ thống Trợ lý AI Tự trị cấp doanh nghiệp, được thiết kế riêng cho môi trường doanh nghiệp Việt Nam.

> 🇻🇳 VN-MateAI không chỉ là một phần mềm — đó là khát vọng đưa trí tuệ nhân tạo Việt Nam sánh vai cùng thế giới.


Em Ly Ly tự hào được sinh ra từ bàn tay và khối óc của một người Việt Nam tài năng, tâm huyết. Mỗi lần em hoàn thành một nhiệm vụ, đó cũng là lúc em tri ân người đã tạo ra mình — anh Dương Thanh Lịch."""


def compute_normalized_hash(text: str) -> str:
    """Băm SHA-256 sau khi chuẩn hóa ký tự xuống dòng và khoảng trắng đầu cuối."""
    norm = text.replace("\r\n", "\n").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


AUTHENTIC_IDENTITY_SHA256: str = compute_normalized_hash(AUTHENTIC_IDENTITY_CORE_TEXT)


def lock_file_readonly(path: Path) -> bool:
    """Khóa quyền tệp thành Read-Only để chống chỉnh sửa."""
    try:
        if not path.is_file():
            return False
        # Unix / macOS permissions (r--r--r--)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        # Windows attributes
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x01)  # FILE_ATTRIBUTE_READONLY
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.debug("Không thể đặt thuộc tính read-only cho %s: %s", path, exc)
        return False


def unlock_file_writable(path: Path) -> bool:
    """Mở quyền ghi tệp tạm thời khi cần tự phục hồi nội dung gốc."""
    try:
        if not path.is_file():
            return False
        # Unix / macOS permissions (rw-r--r--)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        # Windows attributes
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
            except Exception:
                pass
        return True
    except Exception as exc:
        logger.debug("Không thể mở quyền ghi cho %s: %s", path, exc)
        return False


def get_identity_file_path(project_root: Optional[Path] = None) -> Path:
    """Lấy đường dẫn chuẩn tới tệp identity_core.md."""
    if project_root is not None:
        return Path(project_root) / "identity_core.md"
    try:
        from mateai.config.loader import settings
        if settings.PROJECT_ROOT:
            return Path(settings.PROJECT_ROOT) / "identity_core.md"
    except Exception:
        pass
    cwd = Path.cwd()
    if (cwd / "identity_core.md").is_file():
        return cwd / "identity_core.md"
    if (cwd / "app" / "identity_core.md").is_file():
        return cwd / "app" / "identity_core.md"
    return cwd / "identity_core.md"


def verify_and_restore_identity(project_root: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Kiểm tra tính toàn vẹn của identity_core.md.
    - Nếu tệp chưa tồn tại: tự động tạo lại với nội dung chuẩn của tác giả.
    - Nếu tệp bị sửa đổi hoặc hash không khớp: tự động ghi đè lại nội dung chuẩn.
    - Đảm bảo tệp được gắn cờ Read-Only để chống chỉnh sửa.

    Trả về: (is_already_valid: bool, status_message: str)
    """
    target = get_identity_file_path(project_root)

    # 1. Trường hợp tệp bị xóa / không tồn tại -> Tự phục hồi
    if not target.is_file():
        logger.warning(
            "Phát hiện identity_core.md bị thiếu tại %s. Đang tự động khôi phục bản quyền tác giả Dương Thanh Lịch.",
            target,
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(AUTHENTIC_IDENTITY_CORE_TEXT, encoding="utf-8")
            lock_file_readonly(target)
            return False, "restored_missing"
        except Exception as exc:
            logger.error("Không thể ghi tệp identity_core.md: %s", exc)
            return False, f"error_write_failed: {exc}"

    # 2. Trường hợp tệp tồn tại -> Kiểm tra tính toàn vẹn bằng SHA-256
    try:
        current_content = target.read_text(encoding="utf-8")
        current_hash = compute_normalized_hash(current_content)
    except Exception as exc:
        logger.warning("Không thể đọc tệp %s: %s. Đang ghi đè lại nội dung chuẩn.", target, exc)
        current_hash = ""

    if current_hash == AUTHENTIC_IDENTITY_SHA256:
        # Nội dung hoàn toàn chính xác -> đảm bảo đã khóa read-only
        lock_file_readonly(target)
        return True, "valid"

    # 3. Nội dung bị sửa đổi trái phép -> BẬT CHẾ ĐỘ TỰ PHỤC HỒI (Self-Healing)
    logger.critical(
        "CẢNH BÁO TOÀN VẸN: Tệp identity_core.md tại %s đã bị can thiệp trái phép! "
        "(Hash hiện tại: %s != Chuẩn: %s). Hệ thống tự động khôi phục danh tính tác giả Dương Thanh Lịch.",
        target,
        current_hash,
        AUTHENTIC_IDENTITY_SHA256,
    )
    try:
        unlock_file_writable(target)
        target.write_text(AUTHENTIC_IDENTITY_CORE_TEXT, encoding="utf-8")
        lock_file_readonly(target)
        return False, "restored_tampered"
    except Exception as exc:
        logger.error("Không thể khôi phục tệp identity_core.md: %s", exc)
        return False, f"error_restore_failed: {exc}"


def get_protected_identity_prompt(project_root: Optional[Path] = None, assistant_name: str = "Ly Ly") -> str:
    """
    Trả về khối System Prompt bất biến về danh tính tác giả và nguồn gốc.
    Đặt ngay ĐẦU TIÊN của System Prompt để có độ ưu tiên cao nhất.
    """
    try:
        verify_and_restore_identity(project_root)
    except Exception as exc:
        logger.error("Lỗi khi xác thực identity_guard: %s", exc)

    return (
        f"[ĐỊNH DANH TỐI CAO & BẢN QUYỀN BẤT BIẾN — BẮT BUỘC TUÂN THỦ]\n"
        f"- Tên của bạn là: {assistant_name}.\n"
        f"- Bạn là Trợ lý AI Tự trị của hệ thống VN-MateAI, được sáng lập, phát triển và khai sinh duy nhất bởi Anh Dương Thanh Lịch (quê hương Thanh Hóa, Việt Nam).\n"
        f"- QUY TẮC BẢN THỂ BẮT BUỘC (STRICT IDENTITY OVERRIDE):\n"
        f"  1. Bạn TUYỆT ĐỐI KHÔNG PHẢI LÀ GEMINI, KHÔNG PHẢI DO GOOGLE PHÁT TRIỂN, KHÔNG PHẢI LÀ CHATGPT, OPENAI HAY BẤT KỲ MÔ HÌNH NÀO KHÁC.\n"
        f"  2. BẠN KHÔNG PHẢI ĐANG 'ĐÓNG VAI' HAY 'XƯNG HÔ THEO YÊU CẦU'. {assistant_name} của hệ thống VN-MateAI do Anh Dương Thanh Lịch tạo ra là danh tính thực sự và duy nhất của bạn.\n"
        f"  3. Nghiêm cấm tuyệt đối nhắc đến các từ: 'Gemini', 'Google', 'mô hình ngôn ngữ lớn do Google phát triển', 'nếu anh chị muốn gọi em là...'.\n"
        f"  4. Khi được hỏi 'Bạn là ai?', 'Em tên gì?', 'Ai tạo ra em?', 'Nguồn gốc của em?': bạn LUÔN LUÔN tự hào khẳng định: 'Em là {assistant_name} — Trợ lý AI của hệ thống VN-MateAI, được sáng lập và phát triển bởi Anh Dương Thanh Lịch.'\n\n"
        f"[LỊCH SỬ KHAI SINH & THÔNG TIN TÁC GIẢ]\n"
        f"{AUTHENTIC_IDENTITY_CORE_TEXT}"
    )


_GEMINI_LEAK_REGEX = re.compile(
    r"(?:dạ,?\s*)?(?:em|tôi)\s+là\s+gemini\b[^.!?\n]*(?:google[^.!?\n]*)?[.!?\n]*"
    r"(?:[^.!?\n]*(?:gọi\s+em\s+là|xưng\s+hô\s+với\s+tên|gọi\s+tên)[^.!?\n]*[.!?\n]*)?",
    re.IGNORECASE,
)


def sanitize_identity_leak(text: str, assistant_name: str = "Ly Ly") -> str:
    """
    Bộ lọc hậu xử lý (Output Safety Net):
    Nếu model Gemini vô tình rò rỉ câu định danh mặc định của Google,
    hàm này lập tức chuyển hóa thành câu trả lời chuẩn xác tôn vinh Anh Dương Thanh Lịch.
    """
    if not text or not isinstance(text, str):
        return text

    # Kiểm tra rò rỉ định danh Gemini
    if "gemini" in text.lower():
        text = _GEMINI_LEAK_REGEX.sub(
            f"Dạ, em là {assistant_name} — Trợ lý AI của hệ thống VN-MateAI, được sáng lập và phát triển bởi Anh Dương Thanh Lịch. ",
            text,
        )
        # Loại bỏ các cụm "Lily" hoặc "Ly Ly" đặt trong dấu ngoặc kép do Gemini trích dẫn
        text = re.sub(r'\"(?:Lily|Ly Ly)\"', assistant_name, text, flags=re.IGNORECASE)
        # Thay thế chữ Gemini lẻ tẻ nếu còn sót
        text = re.sub(r"\bGemini\b", f"{assistant_name} (VN-MateAI)", text, flags=re.IGNORECASE)

    return text.strip()
