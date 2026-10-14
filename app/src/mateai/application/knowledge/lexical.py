# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/knowledge/lexical.py
=========================
Công cụ so khớp từ khoá dùng chung cho mọi lớp truy vết tri thức
(Đồ thị GraphRAG, BM25, và lọc kết quả Vector của ChromaDB).

Vì sao cần module này
---------------------
Hệ thống có ba lớp tìm kiếm song song, và trước đây mỗi lớp tự quyết định
"cái này có liên quan không" theo cách riêng — dẫn tới kết quả mâu thuẫn:
cùng một câu hỏi, đồ thị báo có, RAG báo không, BM25 báo có. Ngưỡng bằng
chứng phải **giống nhau ở cả ba lớp**, nên gói vào một chỗ.

Vì sao ngưỡng dựa trên từ khoá chứ không dựa trên cosine
-------------------------------------------------------
ChromaDB mặc định dùng `ONNX MiniLM-L6-v2` — một mô hình **tiếng Anh**.
Đo thực tế trên kho quy chế tiếng Việt của hệ thống:

    Truy vấn                                        cosine cao nhất
    -----------------------------------------------  ---------------
    "Quy trình xin nghỉ phép từ 2 đến 3 ngày"   (HỢP LỆ)      0.351
    "Quy trình cấp bằng lái xe máy điện tử..."  (VÔ NGHĨA)    0.373
    "Thủ tục nghỉ ốm và bồi hoàn bảo hiểm"     (HỢP LỆ)      0.257
    "Ai phát minh bóng đèn Edison"              (VÔ NGHĨA)    0.149
    "Nhân viên cần làm gì để truy cập database" (HỢP LỆ)      0.071

Câu hợp lệ nhất (0.071) lại thấp hơn câu vô nghĩa (0.373) — **không tồn tại
một ngưỡng cosine nào phân biệt được**. Đây không phải lỗi cấu hình mà là hạn
chế của mô hình tiếng Anh trên văn bản tiếng Việt có dấu.

Vì vậy chiến lược là: **BM25 + đồ thị quyết định "có căn cứ hay không", vector
chỉ dùng để XẾP HẠNG và bổ sung ngữ cảnh.** Không dùng cosine làm cổng
chống ảo giác vì cổng đó không hoạt động. Nếu sau này cài mô hình đa ngôn ngữ
(`intfloat/multilingual-e5-small`) thì mới có thể dùng lại cosine làm cổng.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Set, Tuple

# Ngưỡng bằng chứng mặc định — áp dụng đồng nhất cho cả 3 lớp.
#   MIN_HITS      : số từ khoá nội dung tối thiểu phải trùng
#   MIN_COVERAGE  : tỉ lệ từ khoá truy vấn được giải thích bằng văn bản
#
# MIN_COVERAGE là điều kiện then chốt. Nếu chỉ đòi 2 từ trùng thì câu hỏi
# dài sẽ dễ "trúng" nhầm: "Quy trình cấp bằng lái xe máy điện tử cho tàu
# ngầm" (10 từ nội dung) chỉ cần trùng 2 từ là đạt — và thực tế nó trùng
# {"điện", "từ"} với triplet "NỘP HÓA ĐƠN ĐIỆN TỬ VAT", tức là 2/10 = 20%.
# Đòi phủ 30% token thì câu đó bị loại, còn câu thật ("Quy trình xin nghỉ
# phép từ 2 đến 3 ngày", 5/6 = 83%) vẫn qua.
MIN_HITS = 2
MIN_COVERAGE = 0.30

# Ngưỡng nới lỏng cho truy vấn cực ngắn (1-2 từ nội dung): lúc đó 1 từ trùng
# đã là toàn bộ ý nghĩa của câu hỏi, đòi 30% là vô nghĩa.
SHORT_QUERY_MAX_TOKENS = 2
MIN_HITS_SHORT_QUERY = 1

# Từ chức năng tiếng Việt: xuất hiện ở mọi câu hỏi nên không mang thông tin.
# Ghi ra có dấu cho dễ đọc; sẽ được chuẩn hoá về dạng không dấu ở bước khởi tạo.
#
# Lưu ý về các mục "domain-common": "cấp" (cấp bằng lái / cấp quyền / cấp tài
# khoản), "duyệt", "chuyển", "gửi" là động từ nghiệp vụ xuất hiện ở HẦU HẾT
# các triplet, nên chúng không phân biệt được văn bản nào với văn bản nào.
# Giữ chúng lại sẽ khiến ngưỡng bằng chứng dễ bị lừa bởi cụm từ ngẫu nhiên.
# Việc loại chúng không làm mất khả năng tra cứu vì từ chức năng không bao
# giờ là đáp án — thực thể ("Nhân viên", "CFO", "BHXH") và con số
# ("10.000.000", "48 giờ") mới mang thông tin.
_STOPWORDS_RAW = {
    # -- chức năng chung --
    "của", "cho", "và", "hoặc", "là", "có", "không", "được", "phải", "này",
    "đó", "khi", "nếu", "thì", "mà", "với", "các", "những", "một", "tôi",
    "em", "anh", "chị", "sếp", "bạn", "nhé", "ạ", "gì", "thế", "nào",
    "bao", "nhiêu", "làm", "ra", "vào", "lên", "xuống", "qua", "tại", "theo",
    "ai", "như", "cũng", "rồi", "đây", "sẽ", "đang", "mọi", "tất", "cả",
    "biết", "thao", "tác", "cần", "gồm", "từ", "trong", "ngoài", "sau",
    "trước", "trên", "dưới", "bằng", "để", "vì", "nên", "hay", "mỗi",
    "cụ", "thể", "loại", "số", "các", "the", "cac",
    # -- từ chức năng nghiệp vụ: xuất hiện ở gần như mọi triplet --
    "quy", "trình", "cấp", "duyệt", "chuyển", "gửi", "nộp", "đề", "nghị",
    "thông", "báo", "ký", "thực", "hiện", "lưu", "trữ", "quản", "lý",
    "bảo", "mật", "dùng", "sử", "tạo", "ghi", "nhận", "thanh", "toán",
    "truy", "cập", "phê", "bộ", "phận", "giám", "đốc", "trưởng", "quản",
    "lý", "nhân", "viên", "giám", "đốc", "trưởng", "cổng", "chuyển",
}

_DIACRITIC_MAP = {
    "à": "a", "á": "a", "ả": "a", "ã": "a", "ạ": "a", "â": "a", "ầ": "a", "ấ": "a",
    "ẩ": "a", "ẫ": "a", "ậ": "a", "ă": "a", "ằ": "a", "ắ": "a", "ẳ": "a", "ẵ": "a",
    "ặ": "a", "è": "e", "é": "e", "ẻ": "e", "ẽ": "e", "ẹ": "e", "ê": "e", "ề": "e",
    "ế": "e", "ể": "e", "ễ": "e", "ệ": "e", "ì": "i", "í": "i", "ỉ": "i", "ĩ": "i",
    "ị": "i", "ò": "o", "ó": "o", "ỏ": "o", "õ": "o", "ọ": "o", "ô": "o", "ồ": "o",
    "ố": "o", "ổ": "o", "ỗ": "o", "ộ": "o", "ơ": "o", "ờ": "o", "ớ": "o", "ở": "o",
    "ỡ": "o", "ợ": "o", "ù": "u", "ú": "u", "ủ": "u", "ũ": "u", "ụ": "u", "ư": "u",
    "ừ": "u", "ứ": "u", "ử": "u", "ữ": "u", "ự": "u", "ỳ": "y", "ý": "y", "ỷ": "y",
    "ỹ": "y", "ỵ": "y", "đ": "d",
}


def strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp ổn định.

    Dùng `unicodedata` thay vì tra cứu thủ công: bảng `_DIACRITIC_MAP` chỉ phủ
    ký tự dựng sẵn, còn văn bản thực tế có thể dùng **dấu tổ hợp**
    (U+1EA0..U+1EF9) — `unicodedata.normalize("NFD", ...)` rồi bỏ dấu
    thanh sẽ xử lý đúng cả hai, kể cả các từ nhập từ máy chủ dùng Unicode
    khác chuẩn.
    """
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    # Bỏ các dấu thanh / dấu phụ tổ hợp (U+0300..U+036F).
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Ký tự Đ/đ không tách được bằng NFD -> thay thủ công.
    return stripped.replace("đ", "d")


# Stopword cũng phải ở dạng không dấu để so được với token đã bỏ dấu.
STOPWORDS: Set[str] = {strip_diacritics(w) for w in _STOPWORDS_RAW}


def content_tokens(text: Any) -> List[str]:
    """Tách từ nội dung đã bỏ dấu, loại stopword và từ 1 ký tự.

    Nhận cả `str` lẫn `list[str]` (dùng cho danh sách từ khoá đã tách sẵn).
    Bỏ dấu giúp "Quy trình" khớp được cả "quy trình" lẫn "quy trinh" (tài liệu
    nhập tay hay gõ thiếu dấu).
    """
    if text is None:
        return []
    raw = text if isinstance(text, str) else " ".join(str(t) for t in text)
    flat = strip_diacritics(raw)
    tokens = re.findall(r"\b[a-z0-9]{2,}\b", flat)
    return [t for t in tokens if t not in STOPWORDS]


def lexical_evidence(
    query: Any,
    candidate: Any,
    min_hits: int = MIN_HITS,
    min_coverage: float = MIN_COVERAGE,
) -> Dict[str, Any]:
    """
    Đo bằng chứng từ khoá giữa truy vấn và một đoạn văn bản.

    Trả về dict gồm:
        hits        : số từ khoá nội dung trùng (đã khử trùng lặp)
        coverage    : hits / tổng từ khoá truy vấn, trong [0, 1]
        matched     : danh sách từ đã trùng (đã sắp xếp)
        query_tokens: tổng số từ khoá truy vấn
        enough      : đã đủ bằng chứng để coi là "có căn cứ" hay chưa

    Quy tắc đủ bằng chứng:
        - Truy vấn ngắn (<= SHORT_QUERY_MAX_TOKENS từ nội dung): cần
          MIN_HITS_SHORT_QUERY từ trùng — vì 1 từ đã là toàn bộ ý nghĩa.
        - Truy vấn dài: cần đồng thời đạt `min_hits` VÀ phủ `min_coverage`
          tổng số từ khoá. Điều kiện "phủ" chính là chống trùng hợp ngẫu
          nhiên trên câu hỏi dài.
    """
    q_tokens = content_tokens(query)
    c_tokens = set(content_tokens(candidate))

    if not q_tokens:
        # Không tách được từ khoá nào (ví dụ truy vấn toàn ký hiệu lạ) thì
        # không thể kết luận bằng từ khoá -> trả về không đủ bằng chứng.
        return {
            "hits": 0, "coverage": 0.0, "matched": [],
            "query_tokens": 0, "enough": False,
        }

    matched = sorted(set(q_tokens) & c_tokens)
    hits = len(matched)
    coverage = hits / len(q_tokens)

    if len(q_tokens) <= SHORT_QUERY_MAX_TOKENS:
        enough = hits >= MIN_HITS_SHORT_QUERY
    else:
        enough = hits >= min_hits and coverage >= min_coverage

    return {
        "hits": hits,
        "coverage": round(coverage, 3),
        "matched": matched,
        "query_tokens": len(q_tokens),
        "enough": enough,
    }


def lexical_evidence_gate(
    query: Any,
    candidate: Any,
    min_hits: int = MIN_HITS,
    min_coverage: float = MIN_COVERAGE,
) -> Tuple[bool, Dict[str, Any]]:
    """Bọc `lexical_evidence` trả về (đủ bằng chứng, chi tiết)."""
    ev = lexical_evidence(query, candidate, min_hits=min_hits, min_coverage=min_coverage)
    return ev["enough"], ev
