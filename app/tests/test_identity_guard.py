# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_identity_guard.py
============================
Kiểm thử cơ chế bảo vệ danh tính và tác quyền bất biến của anh Dương Thanh Lịch
trong VN-MateAI (identity_core.md & System Prompt).
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mateai.application.security.identity_guard import (
    AUTHENTIC_IDENTITY_CORE_TEXT,
    AUTHENTIC_IDENTITY_SHA256,
    compute_normalized_hash,
    get_protected_identity_prompt,
    lock_file_readonly,
    unlock_file_writable,
    verify_and_restore_identity,
)


def test_authentic_hash_matches():
    """Mã băm chuẩn phải khớp chính xác với nội dung gốc."""
    h = compute_normalized_hash(AUTHENTIC_IDENTITY_CORE_TEXT)
    assert h == AUTHENTIC_IDENTITY_SHA256
    assert "Dương Thanh Lịch" in AUTHENTIC_IDENTITY_CORE_TEXT
    assert "VN-MateAI" in AUTHENTIC_IDENTITY_CORE_TEXT


def test_verify_on_current_file():
    """Tệp identity_core.md hiện tại phải hợp lệ và được khóa chỉ đọc."""
    valid, status = verify_and_restore_identity(ROOT)
    assert valid is True
    assert status == "valid"

    # Kiểm tra quyền read-only trên hệ thống tệp
    id_file = ROOT / "identity_core.md"
    mode = id_file.stat().st_mode
    # Không có quyền ghi đối với user/group/other (hoặc ít nhất không ghi cho group/other)
    assert not (mode & stat.S_IWOTH)


def test_self_healing_when_tampered(tmp_path):
    """Khi tệp bị kẻ gian sửa đổi trái phép, hệ thống phải tự động phục hồi."""
    test_file = tmp_path / "identity_core.md"
    test_file.write_text("Tác giả giả mạo: John Doe", encoding="utf-8")

    # Xác thực trên thư mục tạm
    valid, status = verify_and_restore_identity(tmp_path)
    assert valid is False
    assert status == "restored_tampered"

    # Tệp phải được khôi phục về tên anh Dương Thanh Lịch
    healed_content = test_file.read_text(encoding="utf-8")
    assert "Dương Thanh Lịch" in healed_content
    assert "John Doe" not in healed_content
    assert compute_normalized_hash(healed_content) == AUTHENTIC_IDENTITY_SHA256


def test_self_healing_when_missing(tmp_path):
    """Khi tệp bị xóa, hệ thống phải tự động tạo lại nguyên vẹn."""
    test_file = tmp_path / "identity_core.md"
    assert not test_file.exists()

    valid, status = verify_and_restore_identity(tmp_path)
    assert valid is False
    assert status == "restored_missing"
    assert test_file.is_file()

    content = test_file.read_text(encoding="utf-8")
    assert "Dương Thanh Lịch" in content
    assert compute_normalized_hash(content) == AUTHENTIC_IDENTITY_SHA256


def test_llm_engine_system_prompt_includes_creator():
    """System prompt của AI luôn bắt buộc chứa tác giả Dương Thanh Lịch và nằm ở đầu tiên."""
    from mateai.application.agent.llm_engine import build_system_prompt

    prompt = build_system_prompt(source_device="portal")
    assert "Dương Thanh Lịch" in prompt
    assert "VN-MateAI" in prompt
    assert "Ly Ly" in prompt
    assert "[ĐỊNH DANH TỐI CAO & BẢN QUYỀN BẤT BIẾN" in prompt
    assert "KHÔNG PHẢI LÀ GEMINI" in prompt
    # Định danh tác giả phải nằm ngay ở đầu prompt (vị trí 0)
    assert prompt.startswith("[ĐỊNH DANH TỐI CAO & BẢN QUYỀN BẤT BIẾN")


def test_sanitize_identity_leak():
    """Lọc triệt để mọi trường hợp model Gemini buột miệng nhận là Google Gemini."""
    from mateai.application.security.identity_guard import sanitize_identity_leak

    leak_input = (
        "Dạ, em là Gemini – mô hình ngôn ngữ lớn do Google phát triển. "
        "Nhưng nếu anh chị muốn gọi em là Lily hoặc muốn em xưng hô với tên Lily trong cuộc trò chuyện này thì em rất sẵn lòng ạ!\n"
        "Hôm nay \"Lily\" có thể giúp gì cho anh chị nào?"
    )

    cleaned = sanitize_identity_leak(leak_input, assistant_name="Ly Ly")
    assert "Gemini" not in cleaned
    assert "Google" not in cleaned
    assert "Dương Thanh Lịch" in cleaned
    assert "Ly Ly" in cleaned


if __name__ == "__main__":
    print("Running test_authentic_hash_matches...")
    test_authentic_hash_matches()
    print("Running test_verify_on_current_file...")
    test_verify_on_current_file()
    print("Running test_self_healing_when_tampered...")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_self_healing_when_tampered(Path(td))
    print("Running test_self_healing_when_missing...")
    with tempfile.TemporaryDirectory() as td:
        test_self_healing_when_missing(Path(td))
    print("Running test_llm_engine_system_prompt_includes_creator...")
    test_llm_engine_system_prompt_includes_creator()
    print("Running test_sanitize_identity_leak...")
    test_sanitize_identity_leak()
    print("ALL TESTS PASSED SUCCESSFULLY! ✅")
