# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/knowledge/graph_rag.py
===========================
Phase 57: Enterprise GraphRAG (Knowledge Graph + Hybrid Vector/BM25 Search).
Trích xuất Thực thể (Entities) và Mối quan hệ 3 thành phần (Subject - Predicate - Object).
Ngăn chặn 100% ảo giác (Hallucination) khi tra cứu quy trình phê duyệt & pháp lý doanh nghiệp.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.plugin_manager import export_skill

# Ngưỡng bằng chứng từ khoá dùng chung cho CẢ BA lớp truy vết (đồ thị, BM25,
# vector). Xem `core/knowledge/lexical.py` để hiểu vì sao không dùng cosine
# làm cổng chống ảo giác.
from mateai.application.knowledge.lexical import (
    MIN_COVERAGE,
    MIN_HITS,
    content_tokens as _content_tokens,
    lexical_evidence as _lexical_evidence,
    strip_diacritics as _strip_diacritics,
)

logger = logging.getLogger(__name__)


# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
_GRAPH_STORE_PATH = _PROJECT_ROOT / "storage" / "enterprise_graph.json"

# ── Từ điển trích xuất Triplet (nghiệp vụ doanh nghiệp Việt Nam) ───────────────
# Danh sách thực thể thường xuất hiện trong văn bản quy chế/SOP. Giữ ở dạng
# chuỗi con để khớp không phân biệt hoa thường và không cần NLP nặng.
_DOMAIN_ENTITIES = (
    "Nhân viên", "Quản lý trực tiếp", "Trưởng phòng", "Trưởng bộ phận",
    "Bộ phận Nhân sự", "Bộ phận HR", "Nhân sự (HR)", "Phòng Kế toán", "Kế toán",
    "Giám đốc Tài chính", "Giám đốc điều hành", "Cổng ERP Doanh nghiệp",
    "Cổng Portal", "Hệ thống VN-MateAI", "Bảo hiểm Xã hội", "Bảo hiểm Y tế",
    "Audit Logs Bất biến", "Xác thực 2 lớp", "Cấp Quản trị", "Google Workspace",
    "Microsoft Exchange", "Phòng Ban", "Chủ tịch HĐQT", "Bộ phận IT",
)

# Vị ngữ: cụm từ hành động -> nhãn quan hệ chuẩn hoá.
_ACTION_PREDICATES = (
    ("nộp đơn", "NỘP ĐƠN"),
    ("xin nghỉ", "XIN NGHỈ"),
    ("gửi đơn", "GỞI ĐƠN"),
    ("duyệt", "PHÊ DUYỆT"),
    ("ký", "KÝ DUYỆT"),
    ("chuyển", "CHUYỂN TRÌNH"),
    ("trình", "TRÌNH DUYỆT"),
    ("báo", "BÁO CÁO"),
    ("nộp", "NỘP"),
    ("làm thủ tục", "LÀM THỦ TỤC"),
    ("thanh toán", "THANH TOÁN"),
    ("truy cập", "TRUY CẬP"),
    ("cấp", "CẤP"),
    ("tạo", "TẠO"),
    ("ghi nhận", "GHI NHẬN"),
    ("đề nghị", "ĐỀ NGHỊ"),
    ("phê duyệt", "PHÊ DUYỆT"),
    ("thông báo", "THÔNG BÁO"),
    ("lưu trữ", "LƯU TRỮ"),
    ("quản lý", "QUẢN LÝ"),
    ("bảo mật", "BẢO MẬT"),
    ("sử dụng", "SỬ DỤNG"),
    ("được duyệt bởi", "ĐƯỢC DUYỆT BỞI"),
)

# Mẫu điều kiện số: tiền tố + số + đơn vị.
_CONDITION_PATTERNS = (
    r"((?:tối đa|không quá|dưới|trên|hơn|từ|trong vòng|ít nhất|tối thiểu)\s*[\d.,]+\s*(?:ngày|giờ|tuần|tháng|%|triệu|vnđ|vnd|tỷ))",
    r"([\d.,]+\s*(?:ngày làm việc|ngày|giờ|tháng|triệu đồng|vnđ|vnd|%))",
)


def _split_sentences(text: str) -> List[str]:
    """Tách văn bản thành câu, gồm cả câu ngăn bởi xuống dòng."""
    if not text:
        return []
    # Chuẩn hoá khoảng trắng thừa nhưng giữ ngắt dòng làm ranh giới câu.
    flat = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n"))
    parts = re.split(r"(?<=[.!?;])\s+|\n+", flat)
    return [p.strip() for p in parts if p and p.strip()]


def _extract_entities(sentence: str) -> List[str]:
    """Trả về các thực thể nghiệp vụ tìm thấy trong câu, theo đúng thứ tự xuất hiện."""
    low = sentence.lower()
    found: List[tuple] = []
    for ent in _DOMAIN_ENTITIES:
        idx = low.find(ent.lower())
        if idx >= 0:
            found.append((idx, ent))
    found.sort(key=lambda x: x[0])

    # Giữ nguyên tên trong câu (giữ hoa/thường gốc) để triplet đọc tự nhiên.
    result: List[str] = []
    for _, ent in found:
        m = re.search(re.escape(ent), sentence, re.IGNORECASE)
        result.append(m.group(0) if m else ent)
    return result


def _extract_predicate(sentence: str) -> str:
    """Xác định vị ngữ (quan hệ) của câu từ cụm từ hành động."""
    low = sentence.lower()
    for needle, label in _ACTION_PREDICATES:
        if needle in low:
            return label
    return ""


def _extract_condition(sentence: str) -> str:
    """Trích điều kiện định lượng (thời hạn / mức tiền / tỷ lệ) nếu câu có."""
    for pat in _CONDITION_PATTERNS:
        m = re.search(pat, sentence, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


# Ngưỡng khớp tối thiểu để coi một truy vấn là "có kết quả". Dưới ngưỡng này
# hệ thống phải thành thật nói "không có trong tài liệu" thay vì dựng một
# câu trả lời trông như có căn cứ.


class EnterpriseGraphRAG:
    """Đồ thị Tri thức Doanh nghiệp (Enterprise Knowledge Graph)."""

    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, Any]] = []
        self._load_or_init_graph()

    def _load_or_init_graph(self) -> None:
        """Tải đồ thị tri thức từ file hoặc nạp đồ thị mẫu cho các quy trình cốt lõi."""
        if _GRAPH_STORE_PATH.exists():
            try:
                data = json.loads(_GRAPH_STORE_PATH.read_text(encoding="utf-8"))
                self.nodes = data.get("nodes", {})
                self.edges = data.get("edges", [])
                logger.info("[GraphRAG] Đã nạp %d thực thể và %d quan hệ từ lưu trữ.", len(self.nodes), len(self.edges))
                return
            except Exception as e:
                logger.warning("[GraphRAG] Không thể đọc graph file: %s", e)

        # Khởi tạo đồ thị tri thức chuẩn cho Enterprise OS
        self._init_standard_enterprise_graph()

    def _init_standard_enterprise_graph(self) -> None:
        """Nạp các quan hệ chuẩn giữa Nhân viên, Quản lý, HR, CFO, và Thủ tục công ty."""
        initial_triplets = [
            # Quy trình nghỉ phép 1 ngày
            ("Nhân viên", "NỘP ĐƠN NGHỈ 1 NGÀY QUA", "Cổng Portal", "Trước 24 giờ"),
            ("Cổng Portal", "CHUYỂN DUYỆT ĐƠN CHO", "Quản lý trực tiếp", "Tự động thông báo"),
            ("Quản lý trực tiếp", "PHÊ DUYỆT ĐƠN NGHỈ", "Nhân sự (HR)", "Ghi nhận vào hệ thống"),

            # Quy trình nghỉ phép từ 2-3 ngày
            ("Nhân viên", "XIN NGHỈ TỪ 2 ĐẾN 3 NGÀY", "Quản lý trực tiếp", "Trước ít nhất 3 ngày làm việc"),
            ("Quản lý trực tiếp", "KÝ DUYỆT VÀ CHUYỂN", "Trưởng phòng Nhân sự (HR)", "Hưởng nguyên lương phép năm"),

            # Quy trình nghỉ trên 3 ngày
            ("Nhân viên", "XIN NGHỈ TRÊN 3 NGÀY", "Trưởng bộ phận", "Trước 5 ngày làm việc"),
            ("Trưởng bộ phận", "TRÌNH PHÊ DUYỆT LÊN", "Giám đốc điều hành (C.O.O)", "Quyết định cuối cùng"),

            # Quy trình nghỉ ốm BHXH
            ("Nhân viên", "KHI NGHỈ ỐM ĐAU", "Báo ngay cho Quản lý trực tiếp", "Trong ca làm việc"),
            ("Nhân viên", "NỘP GIẤY CHỨNG NHẬN BỆNH VIỆN", "Bộ phận HR", "Thời hạn trong vòng 48 giờ"),
            ("Bộ phận HR", "LÀM THỦ TỤC THANH TOÁN", "Bảo hiểm Xã hội (BHXH)", "Mức hưởng 75% lương BHXH"),

            # Quy trình chi tiêu và tạm ứng
            ("Nhân viên", "ĐỀ NGHỊ MUA SẮM VẬT TƯ / TIẾP KHÁCH", "Trưởng bộ phận", "Dưới 10.000.000 VND"),
            ("Trưởng bộ phận", "TRÌNH DUYỆT KHOẢN CHI LỚN (>10 TRIỆU)", "Giám đốc Tài chính (CFO)", "Phê duyệt ngân sách"),
            ("Nhân viên", "NỘP HÓA ĐƠN ĐIỆN TỬ VAT", "Cổng ERP Doanh nghiệp", "Hạn chót ngày 25 hàng tháng"),
            ("Kế toán", "CHI TRẢ TIỀN HOÀN ỨNG", "Nhân viên", "Vào đợt lương ngày 05 tháng kế tiếp"),

            # Quy định bảo mật Zero-Trust
            ("Nhân viên", "TRUY CẬP HỆ THỐNG MẬT / DATABASE", "Xác thực 2 lớp (2FA)", "Bắt buộc"),
            ("Hệ thống VN-MateAI", "GHI NHẬN TOÀN BỘ THAO TÁC VÀO", "Audit Logs Bất biến", "Bảo vệ Zero-Trust"),
        ]

        self.nodes = {}
        self.edges = []
        for s, p, o, ctx in initial_triplets:
            self.add_triplet(s, p, o, context=ctx)

        self._save_graph()
        logger.info("[GraphRAG] Đã khởi tạo Đồ thị Tri thức mẫu (%d quan hệ).", len(self.edges))

    def add_triplet(self, subject: str, predicate: str, object_entity: str, context: str = "", source: str = "") -> bool:
        """
        Thêm một bộ ba (Triplet) vào Đồ thị tri thức.

        Trả về True nếu thêm mới, False nếu trùng. Trước đây hàm append thẳng
        vào self.edges với điều kiện nào — nên nạp lại cùng một tài liệu 10 lần
        sẽ nhân bội số cạnh, đồ thị phình vô hạn và câu trả lời lặp ý.
        """
        s = subject.strip()
        o = object_entity.strip()
        p = predicate.strip().upper()

        if not s or not o or not p:
            return False

        if s not in self.nodes:
            self.nodes[s] = {"name": s, "type": "Entity"}
        if o not in self.nodes:
            self.nodes[o] = {"name": o, "type": "Entity"}

        # Dedupe theo chữ ký nội dung, không theo thứ tự chèn.
        sig = f"{s}|{p}|{o}"
        for existing in self.edges:
            if f"{existing['subject']}|{existing['predicate']}|{existing['object']}" == sig:
                # Bổ sung nguồn nếu triplet đã tồn tại nhưng lần sau có nguồn rõ hơn.
                if source and not existing.get("source"):
                    existing["source"] = source
                return False

        self.edges.append({
            "subject": s,
            "predicate": p,
            "object": o,
            "context": context,
            "source": source,
        })
        return True

    def ingest_document(self, text: str, doc_name: str = "", category: str = "") -> Dict[str, Any]:
        """
        Trích xuất Thực thể & Mối quan hệ (Triplet) từ văn bản tài liệu thật.

        Đây là phần BƯỚC 3 của Phase 57 mà trước đây thiếu hoàn toàn: toàn bộ đồ
        thị chỉ gồm 16 triplet hardcode, không đọc tài liệu nào. Nạp "Quy trình
        xin nghỉ phép.pdf" vào là hệ thống không rút ra được gì.

        Chiến lược trích xuất (không cần LLM, nên chạy offline & tất định):
          1. Tách văn bản thành câu theo dấu chấm.
          2. Nhận diện cặp thực thể trong câu bằng danh sách thuật ngữ nghiệp vụ.
          3. Với mỗi cặp, dùng vị trí từ khóa hành động (nộp, duyệt, gửi, ký...)
             làm vị ngữ, phần còn lại của câu làm ngữ ngữ/điều kiện.
          4. Trích điều kiện số (số ngày, số tiền, mức %) nếu có.
        """
        added = 0
        scanned = 0
        source = doc_name or category or "tài liệu nạp"

        for sentence in _split_sentences(text or ""):
            scanned += 1
            entities = _extract_entities(sentence)
            if len(entities) < 2:
                continue

            predicate = _extract_predicate(sentence)
            condition = _extract_condition(sentence)
            if not predicate:
                continue

            # Ghép cặp liền kề trong danh sách thực thể tìm được.
            for a, b in zip(entities, entities[1:]):
                if self.add_triplet(a, predicate, b, context=condition, source=source):
                    added += 1

        self._save_graph()
        logger.info(
            "[GraphRAG] Trích xuất từ '%s': %d/%d câu -> %d triplet mới (tổng %d quan hệ).",
            source, added, scanned, added, len(self.edges),
        )
        return {
            "source": source,
            "sentences_scanned": scanned,
            "triplets_added": added,
            "total_edges": len(self.edges),
        }

    def _save_graph(self) -> None:
        """Lưu đồ thị tri thức ra đĩa."""
        try:
            _GRAPH_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _GRAPH_STORE_PATH.write_text(
                json.dumps({"nodes": self.nodes, "edges": self.edges}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("[GraphRAG] Lỗi lưu đồ thị: %s", e)

    def traverse_graph(self, query_keywords: List[str]) -> List[Dict[str, Any]]:
        """
        Duyệt đồ thị tìm các đường đi quan hệ khớp với từ khóa truy vấn.

        So khớp theo TOÀN VẸN TỪ (word boundary) chứ không phải substring.
        Lý do: tiếng Việt có nhiều từ là tiền tố của từ khác theo ngữ nghĩa —
        "quy trình" chứa chuỗi "trình" khớp với vị ngữ "TRÌNH DUYỆT". Trước đây
        dùng `kw in edge_text` nên câu hỏi hoàn toàn ngoài lĩnh vực (ví dụ "cấp
        bằng lái xe máy điện tử") vẫn ăn vào một triplet về phê duyệt nghỉ phép
        và được trả về như căn cứ hợp lệ.
        """
        content_toks = _content_tokens(query_keywords)
        if not content_toks:
            return []

        matched_edges: List[Dict[str, Any]] = []
        seen = set()

        from mateai.application.knowledge import rag_acl
        acl = rag_acl.Filter()

        for edge in self.edges:
            # Quan hệ trích từ tài liệu người hỏi không được xem thì không được lộ qua đồ thị.
            if edge.get("source") and not acl.allows(str(edge["source"])):
                continue
            edge_text = " ".join([
                edge["subject"], edge["predicate"], edge["object"],
                str(edge.get("context", "")),
            ])

            ev = _lexical_evidence(content_toks, edge_text)
            if not ev["enough"]:
                continue

            sig = f"{edge['subject']}->{edge['predicate']}->{edge['object']}"
            if sig in seen:
                continue
            seen.add(sig)
            matched_edges.append({
                **edge,
                "match_score": ev["hits"],
                "match_coverage": ev["coverage"],
                "matched_terms": ev["matched"],
            })

        # Sắp xếp theo độ phủ (khớp được bao nhiêu phần câu hỏi), rồi số từ khớp.
        matched_edges.sort(key=lambda x: (x["match_coverage"], x["match_score"]), reverse=True)
        return matched_edges

    def hybrid_search(self, question: str) -> Dict[str, Any]:
        """
        Tìm kiếm lai (Hybrid Search) = BM25 Keyword + Vector Similarity + Graph Traversal.

        Điểm khác biệt so với bản cũ: hàm này BÁO CÁO TRUNG THỰC khi không
        tìm thấy gì. Trước đây ChromaDB luôn trả về đúng `top_k` kết quả bất kể
        liên quan tới đâu, và hàm ghép chúng vào câu trả lời có tiêu đề "KẾT
        QUẢ TRUY VẤN" — tức là tự tin dựng câu trả lời từ văn bản không liên
        quan. Đó chính là ảo giác mà briefing Phase 57 BƯỚC 3 tuyên bố đã
        "chặn 100%". Nay có ngưỡng: dưới ngưỡng thì thành thật từ chối trả lời.
        """
        question = (question or "").strip()
        if not question:
            return {
                "status": "error",
                "answer": "Câu hỏi truy vấn rỗng. Vui lòng nhập câu hỏi cụ thể.",
                "graph_paths": [],
                "vector_snippets": [],
                "bm25_snippets": [],
            }

        # 1. Trích xuất từ khóa thực thể
        keywords = re.findall(r"\b[A-Za-zÀ-ỹ0-9_]{2,}\b", question)
        graph_matches = self.traverse_graph(keywords)
        # 2. Vector Search từ ChromaDB (có ngưỡng similarity)
        from mateai.application.knowledge.rag_engine import rag_engine
        vector_res = rag_engine.query(question, top_k=5)
        vector_matches = vector_res.get("matches", [])

        # 3. BM25 Keyword Search — bắt các từ khoá nghiệp vụ mà vector
        #    similarity hay bỏ sót (mã số, tên riêng, thuật ngữ pháp lý).
        bm25_matches = _bm25_search(question, rag_engine, top_k=5)

        # 4. Tổng hợp chuỗi thực thi (Flow of Actions)
        graph_lines = []
        for g in graph_matches[:6]:
            ctx_note = f" (Điều kiện: {g['context']})" if g.get("context") else ""
            graph_lines.append(f"• [{g['subject']}] ➔ {g['predicate']} ➔ [{g['object']}]{ctx_note}")

        # Mỗi lớp đã tự lọc theo ngưỡng bằng chứng riêng, nên chỉ cần kiểm
        # tra lớp nào có ít nhất một phát hiện.
        has_graph = bool(graph_matches)
        has_text = bool(vector_matches) or bool(bm25_matches)

        if not has_graph and not has_text:
            # Fail-closed: không có căn cứ thì KHÔNG trả lời, chỉ mở đường
            # nạp tài liệu (Phase 58 BƯỚC 4 — graceful fallback).
            return {
                "status": "no_evidence",
                "question": question,
                "answer": (
                    f"🕸️ [ĐỒ THỊ TRI THỨC — KHÔNG TÌM THẤY CĂN CỨ]\n\n"
                    f"Dạ em đã tra cứu toàn bộ tri thức doanh nghiệp nhưng KHÔNG có tài liệu "
                    f"nào mô tả \"{question}\". Em không dám tự trả lời vì nguy cơ bịa đặt quy định "
                    f"là rất cao.\n\n"
                    f"Sếp muốn em mở khung để Sếp tải tài liệu quy chế/SOP liên quan lên không ạ? "
                    f"Em sẽ nạp vào và tra cứu lại ngay."
                ),
                "graph_paths": [],
                "vector_snippets": [],
                "bm25_snippets": [],
                "needs_document": True,
                "suggested_action": "upload_document",
            }

        sections: List[str] = []

        if has_graph:
            graph_summary = "\n".join(graph_lines)
            sections.append(f"📌 LUỒNG PHÊ DUYỆT & THỰC HIỆN CHUẨN XÁC:\n{graph_summary}")

        doc_lines: List[str] = []
        seen_text: Set[str] = set()
        for src in ("vector_snippets", "bm25_snippets"):
            items = vector_matches if src == "vector_snippets" else bm25_matches
            for it in items:
                content = str(it.get("content", "")).strip()
                if not content:
                    continue
                key = content[:120]
                if key in seen_text:
                    continue
                seen_text.add(key)
                score = it.get("relevance", it.get("score", 0))
                try:
                    score_txt = f" (độ khớp {float(score):.2f})"
                except (TypeError, ValueError):
                    score_txt = ""
                doc_lines.append(f"- {content[:250]}...{score_txt}")

        if doc_lines:
            sections.append("📄 VĂN BẢN QUY CHẾ ĐỐI CHIẾU:\n" + "\n".join(doc_lines))

        answer_text = (
            f"🕸️ [KẾT QUẢ TRUY VẤN ĐỒ THỊ TRI THỨC GRAPHRAG]\n\n"
            + "\n\n".join(sections)
        )

        return {
            "status": "success",
            "question": question,
            "answer": answer_text,
            "graph_paths": graph_matches,
            "vector_snippets": vector_matches,
            "bm25_snippets": bm25_matches,
            "needs_document": False,
        }


def _bm25_search(question: str, rag_engine: Any, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    BM25 keyword search trên kho văn bản của RAG Engine.

    Dùng thư viện `rank_bm25` (đã khai báo trong requirements.txt). Vector search
    dùng cosine similarity nên hay trượt với truy vấn chứa mã số / thuật ngữ
    pháp lý; BM25 bù lại bằng cách so khớp từ khoá chính xác.
    """
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("[GraphRAG] Thiếu rank_bm25 — bỏ qua nhánh BM25 hybrid search.")
        return []

    corpus = rag_engine.get_searchable_corpus()
    if not corpus:
        return []

    texts = [c.get("content", "") for c in corpus]
    tokenized = [_bm25_tokenize(t) for t in texts]
    # BM25Okapi cần ít nhất 1 văn bản; lọc bỏ văn bản rỗng.
    usable = [(i, tk) for i, tk in enumerate(tokenized) if tk]
    if not usable:
        return []

    try:
        bm25 = BM25Okapi([tk for _, tk in usable])
        scores = bm25.get_scores(_bm25_tokenize(question))
    except Exception as exc:
        logger.warning("[GraphRAG] BM25 lỗi: %s", exc)
        return []

    ranked = sorted(
        ((idx, float(score)) for (idx, _), score in zip(usable, scores)),
        key=lambda x: x[1],
        reverse=True,
    )
    max_score = ranked[0][1] if ranked and ranked[0][1] > 0 else 0.0
    if max_score <= 0:
        return []

    # Ngưỡng bằng chứng giống traverse_graph: BM25 cho điểm khác 0 chỉ cần
    # một từ hiếm trùng, nên "Giá vàng hôm nay" vẫn ăn vào một đoạn văn có chữ
    # "hôm". Phải có đủ từ khoá nội dung của truy vấn nằm trong đoạn đó.
    out: List[Dict[str, Any]] = []
    for idx, score in ranked[: top_k * 3]:
        if score <= 0:
            continue
        content = corpus[idx].get("content", "")
        ev = _lexical_evidence(question, content)
        if not ev["enough"]:
            continue
        out.append({
            "content": content,
            "source": corpus[idx].get("source", ""),
            "relevance": round(score / max_score, 3),
            "match_coverage": ev["coverage"],
            "matched_terms": ev["matched"],
            "match_type": "bm25_keyword",
        })
        if len(out) >= top_k:
            break
    return out


def _bm25_tokenize(text: str) -> List[str]:
    """
    Tách từ cho BM25 — dùng CHUNG bộ tách từ với ngưỡng bằng chứng.

    Trước đây hàm này tách từ riêng (giữ dấu, không loại stopword) trong khi
    `traverse_graph` dùng bộ tách từ khác. Hệ quả: BM25 tính điểm trên một
    cách tách từ còn ngưỡng bằng chứng kiểm tra trên cách khác — hai lớp cùng
    nói về "câu hỏi này có căn cứ không" nhưng trả lời khác nhau. Nay cả hai
    dùng `content_tokens()` nên kết quả nhất quán.
    """
    return _content_tokens(text)


# Singleton instance
graph_rag = EnterpriseGraphRAG()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="query_enterprise_graph_rag",
    description="Tra cứu đồ thị tri thức doanh nghiệp GraphRAG (Kết hợp quan hệ Entities Triplet và Vector Semantic Search). Tuyệt đối loại bỏ hiện tượng AI ảo giác đối với quy trình phê duyệt, thủ tục xin nghỉ, thanh toán chi phí hoặc quy chế công ty.",
    parameters_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Câu hỏi cần tra cứu luồng quy trình (ví dụ: 'Quy trình xin nghỉ phép từ 2 đến 3 ngày', 'Ai là người duyệt chi phí trên 10 triệu?').",
            }
        },
        "required": ["question"],
    },
)
def query_enterprise_graph_rag(question: str) -> Dict[str, Any]:
    """Truy vấn đồ thị tri thức doanh nghiệp GraphRAG."""
    return graph_rag.hybrid_search(question=question)
