# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/audio/acoustic_ack_catalog.py
==================================
Phase 6: Pre-warmed Acoustic ACK Catalog & Context-Aware Selector.

Chức năng:
  - Định nghĩa danh mục câu đệm phản xạ (Acoustic ACK) phong phú, tự nhiên theo ngữ cảnh (Context-Aware).
  - Phân loại theo lĩnh vực:
      1. SYSTEM_OPS: Quản trị hệ thống, máy chủ, tiến trình, dịch vụ.
      2. SEARCH_RAG: Tra cứu tri thức, tìm kiếm tài liệu, hỏi đáp văn bản.
      3. BUSINESS_ANALYTICS: Báo cáo, thống kê, KPI, nhân sự, tài chính.
      4. SECURITY_AUDIT: Quét mạng, bảo mật, lỗ hổng, cổng kết nối.
      5. PC_CONTROL: Thao tác giao diện máy tính, ứng dụng, màn hình.
      6. GENERAL_GENERIC: Câu đệm phổ quát cho các tác vụ kỹ thuật chung.
  - Bộ chọn câu đệm thông minh (select_acoustic_ack) dựa trên từ khóa và ý định.
  - Đảm bảo 100% câu đệm có dấu tiếng Việt chuẩn và được nạp trước vào RAM Cache (0ms).
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Danh mục câu đệm phản xạ theo từng ngữ cảnh
# ---------------------------------------------------------------------------

ACOUSTIC_ACK_CATALOG: Dict[str, List[str]] = {
    # 1. Quản trị hệ thống / Hạ tầng máy chủ
    "SYSTEM_OPS": [
        "Dạ, em đang thực thi lệnh hệ thống ngay ạ.",
        "Sếp đợi em kiểm tra máy chủ một chút nhé.",
        "Đang xử lý tác vụ quản trị hệ thống ạ.",
        "Dạ, em bắt tay vào can thiệp hệ thống ngay ạ.",
    ],
    # 2. Tra cứu tri thức / RAG / Tài liệu
    "SEARCH_RAG": [
        "Dạ, em đang tra cứu cơ sở tri thức ạ.",
        "Sếp đợi em tìm kiếm thông tin tài liệu một chút nhé.",
        "Đang trích xuất dữ liệu từ kho tri thức ạ.",
        "Dạ, em đang tổng hợp tài liệu liên quan ạ.",
    ],
    # 3. Phân tích kinh doanh / Báo cáo / KPI / HR / ERP
    "BUSINESS_ANALYTICS": [
        "Dạ, em đang tổng hợp dữ liệu báo cáo ạ.",
        "Đang phân tích số liệu, sếp đợi em một chút nhé.",
        "Dạ, em đang truy xuất dữ liệu nhân sự và chỉ số KPI ạ.",
        "Đang tính toán số liệu thống kê ạ.",
    ],
    # 4. An ninh mạng / Quét bảo mật / Firewall
    "SECURITY_AUDIT": [
        "Dạ, đang bắt đầu quy trình quét an ninh mạng ạ.",
        "Em đang rà soát kết nối mạng và kiểm tra an toàn ạ.",
        "Đang phân tích lưu lượng và bảo mật hệ thống ạ.",
    ],
    # 5. Điều khiển máy tính / Ứng dụng / Màn hình
    "PC_CONTROL": [
        "Dạ, em đang điều khiển tác vụ trên máy tính ạ.",
        "Đang thao tác ứng dụng, anh đợi một chút nhé.",
        "Dạ, em thực hiện ngay trên màn hình ạ.",
    ],
    # 6. Tác vụ chung / Phổ quát (Round-Robin Fallback)
    "GENERAL_GENERIC": [
        "Dạ, em đang xử lý ngay ạ.",
        "Sếp đợi em kiểm tra một chút nhé.",
        "Đang trích xuất dữ liệu ạ.",
        "Em đang thực thi lệnh, anh đợi em một chút nhé.",
        "Dạ, em bắt tay vào làm ngay ạ.",
    ],
}

# Tổng hợp toàn bộ câu đệm phục vụ pre-warm cache
ALL_ACOUSTIC_ACK_PHRASES: List[str] = [
    phrase for phrases in ACOUSTIC_ACK_CATALOG.values() for phrase in phrases
]

_ack_indices: Dict[str, int] = {cat: 0 for cat in ACOUSTIC_ACK_CATALOG}


# ---------------------------------------------------------------------------
# Text Normalisation Helper
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


# ---------------------------------------------------------------------------
# Context-Aware ACK Selector
# ---------------------------------------------------------------------------

def select_acoustic_ack(
    query: str,
    domain: Optional[str] = None,
) -> str:
    """
    Lựa chọn câu đệm phản xạ phù hợp nhất dựa trên nội dung câu hỏi và ngữ cảnh nghiệp vụ.
    Tránh sự nhàm chán bằng cách luân chuyển (Round-Robin) trong cùng một danh mục.
    """
    global _ack_indices

    clean = _strip_accents(query or "")

    category = "GENERAL_GENERIC"

    # 1. Phát hiện Security Audit
    if any(kw in clean for kw in ["quet mang", "security", "an ninh", "lo hong", "threat", "firewall", "port", "quet cong"]):
        category = "SECURITY_AUDIT"

    # 2. Phát hiện Quản trị hệ thống (System Ops)
    elif any(kw in clean for kw in ["cpu", "ram", "server", "tien trinh", "process", "kill", "restart", "dich vu", "service", "o dia", "disk", "he thong"]):
        category = "SYSTEM_OPS"

    # 3. Phát hiện Báo cáo / KPI / HR / ERP
    elif any(kw in clean for kw in ["kpi", "nhan su", "bao cao", "report", "doanh thu", "luong", "cham cong", "erp", "nhan vien", "tai chinh"]):
        category = "BUSINESS_ANALYTICS"

    # 4. Phát hiện Tra cứu / Tìm kiếm RAG
    elif any(kw in clean for kw in ["tim tai lieu", "quy trinh", "huong dan", "chinh sach", "rag", "tra cuu", "tai lieu", "tim kiem"]):
        category = "SEARCH_RAG"

    # 5. Phát hiện Điều khiển máy tính (PC Control)
    elif any(kw in clean for kw in ["chup man hinh", "mo app", "ung dung", "chuot", "ban phim", "clipboard", "man hinh"]):
        category = "PC_CONTROL"

    # Nếu chỉ định domain từ bên ngoài
    if domain:
        domain_upper = domain.upper()
        if domain_upper in ACOUSTIC_ACK_CATALOG:
            category = domain_upper

    phrases = ACOUSTIC_ACK_CATALOG.get(category, ACOUSTIC_ACK_CATALOG["GENERAL_GENERIC"])
    idx = _ack_indices[category] % len(phrases)
    _ack_indices[category] += 1

    selected_phrase = phrases[idx]
    logger.debug("[AcousticACK] Chọn câu đệm [%s]: '%s' cho query: '%s'", category, selected_phrase, query[:35])
    return selected_phrase


def get_all_ack_phrases() -> List[str]:
    """Trả về toàn bộ danh sách câu đệm để pre-warm."""
    return list(ALL_ACOUSTIC_ACK_PHRASES)
