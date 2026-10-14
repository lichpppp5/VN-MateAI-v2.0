# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/rag_engine.py
==================
Phase 56: Enterprise RAG (Retrieval-Augmented Generation) & Knowledge Base.
Trung tâm Tri thức Doanh nghiệp: Lưu trữ, vector hóa và giải đáp tự động chính sách,
quy trình chuẩn (SOP), quy chế lao động, lương thưởng bằng ChromaDB và Hybrid Search.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings

from core.plugin_manager import export_skill

# Cổng bằng chứng từ khoá dùng chung với GraphRAG / BM25. Xem
# `core/knowledge/lexical.py` — tóm tắt: cosine của Chroma không dùng được
# làm cổng chống ảo giác trên tiếng Việt vì mô hình nhúng mặc định là tiếng Anh.
from mateai.application.knowledge.lexical import lexical_evidence_gate

logger = logging.getLogger(__name__)

# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
_CHROMA_DIR = _PROJECT_ROOT / "storage" / "chroma_db"
_DOCS_DIR = _PROJECT_ROOT / "storage" / "knowledge_docs"
_SEED_DOC_PATH = _DOCS_DIR / "Quy_che_va_chinh_sach_nhan_su_2026.md"


#: Tải tài liệu lên tri thức doanh nghiệp (chuyển từ routers/enterprise.py — Phase 10).
ALLOWED_UPLOAD_EXT = {".pdf", ".docx", ".doc", ".md", ".txt", ".csv"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


class UploadRejected(ValueError):
    """Tệp tải lên không được nhận; `status_code` gợi ý cho tầng HTTP."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def save_upload(original_filename: str, payload: bytes) -> Path:
    """Kiểm tra rồi lưu tệp tải lên vào thư mục tri thức. Trả đường dẫn đã lưu.

    An toàn: chỉ phần mở rộng trong danh sách (chống .py/.sh…), tên tệp chuẩn hoá —
    không giữ đường dẫn do client gửi (chống ../), giới hạn 20 MB."""
    original_name = os.path.basename((original_filename or "").replace("\\", "/")).strip()
    if not original_name:
        raise UploadRejected(400, "Thiếu tên tệp.")
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise UploadRejected(400, f"Định dạng '{ext or 'không xác định'}' không được phép. "
                                  f"Chỉ nhận: {', '.join(sorted(ALLOWED_UPLOAD_EXT))}.")
    if not payload:
        raise UploadRejected(400, "Tệp rỗng, không có dữ liệu để nạp.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise UploadRejected(413, f"Tệp quá lớn ({len(payload) / 1024 / 1024:.1f} MB). Giới hạn 20 MB.")
    # Chuẩn hoá tên: bỏ ký tự lạ, thêm tiền tố thời gian để không đè nhau.
    stem = re.sub(r"[^A-Za-z0-9_\-\.]", "_", Path(original_name).stem)[:80] or "tai_lieu"
    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{stem}{ext}"
    docs_dir = globals()["_DOCS_DIR"]  # đọc lúc gọi (test đổi thư mục được)
    docs_dir.mkdir(parents=True, exist_ok=True)
    dest = docs_dir / safe_name
    try:
        dest.write_bytes(payload)
    except OSError as exc:
        raise UploadRejected(500, f"Không lưu được tệp: {exc}") from exc
    # Bản lưu bền ở object storage (prompt cuối §68): bản cục bộ chỉ là bản làm việc để nạp RAG.
    try:
        from mateai.infrastructure.files.object_storage import object_store
        object_store().put(f"knowledge/{safe_name}", payload)
    except Exception as exc:  # noqa: BLE001 — kho đối tượng lỗi không chặn việc nạp tài liệu
        logger.warning("Không lưu được '%s' vào object storage: %s", safe_name, exc)
    return dest


class EnterpriseRAGEngine:
    """Quản lý vector database ChromaDB và xử lý tìm kiếm tri thức doanh nghiệp."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _DOCS_DIR.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self._client.get_or_create_collection(
            name="company_knowledge_base",
            metadata={"description": "VN-MateAI Enterprise SOPs & Policies"},
        )
        self._init_seed_data()

    def _init_seed_data(self) -> None:
        """Nạp tài liệu quy chế mẫu ban đầu nếu bộ tri thức chưa có dữ liệu."""
        try:
            if self.collection.count() == 0:
                seed_content = """# QUY CHẾ VÀ CHÍNH SÁCH NHÂN SỰ TOÀN DIỆN VN-MATEAI (2026)

## ĐIỀU 1: THỜI GIAN LÀM VIỆC VÀ CHẤM CÔNG
1. Thời gian làm việc chuẩn: Từ 08:30 đến 17:30, từ Thứ Hai đến Thứ Sáu hàng tuần. Nghỉ trưa từ 12:00 đến 13:00.
2. Hình thức chấm công: Chấm công tự động qua khuôn mặt trên Camera AI hoặc qua Cổng Portal / Lệnh thoại VN-MateAI.
3. Đi muộn / Về sớm: Được phép linh hoạt tối đa 15 phút nếu có báo trước trên hệ thống. Quá 3 lần/tháng không có lý do chính đáng sẽ bị trừ 10% thưởng KPI tháng.

## ĐIỀU 2: CHẾ ĐỘ NGHỈ PHÉP NĂM VÀ NGHỈ VIỆC RIÊNG
1. Phép năm: Mỗi nhân viên chính thức có 12 ngày phép năm hưởng nguyên lương. Cứ mỗi 5 năm thâm niên làm việc được cộng thêm 1 ngày phép.
2. Quy trình xin nghỉ:
   - Nghỉ 1 ngày: Đăng ký trên Portal trước 24 giờ để Quản lý trực tiếp phê duyệt.
   - Nghỉ từ 2 đến 3 ngày: Trình Quản lý trực tiếp và gửi HR trước ít nhất 3 ngày làm việc.
   - Nghỉ trên 3 ngày: Cần có sự phê duyệt của Trưởng bộ phận và Giám đốc điều hành (C.O.O).
3. Nghỉ kết hôn: Được nghỉ 03 ngày hưởng nguyên lương. Con kết hôn được nghỉ 01 ngày.

## ĐIỀU 3: CHẾ ĐỘ NGHỈ ỐM ĐAU VÀ KHÁM CHỮA BỆNH
1. Khi bị ốm đau hoặc tai nạn đột xuất: Nhân viên phải thông báo ngay cho Quản lý trực tiếp và gửi giấy xác nhận của cơ sở y tế / bệnh viện cho bộ phận HR trong vòng 48 giờ kể từ khi bắt đầu nghỉ.
2. Chế độ hưởng: Hưởng chế độ ốm đau theo quy định của Bảo hiểm Xã hội (BHXH) Việt Nam (75% mức lương đóng BHXH).
3. Công ty hỗ trợ thêm 100% lương cho 02 ngày ốm đầu tiên trong năm không trừ vào phép năm.

## ĐIỀU 4: CHẾ ĐỘ THAI SẢN VÀ CON NHỎ
1. Lao động nữ sinh con được nghỉ thai sản 06 tháng theo quy định Luật Lao động.
2. Công ty hỗ trợ đặc biệt: Tặng 01 tháng lương cơ bản và gói quà mừng trị giá 5.000.000 VND.
3. Lao động nam có vợ sinh con được nghỉ từ 05 đến 14 ngày làm việc tùy thuộc vào hình thức sinh thường hay sinh mổ.
4. Nhân viên nữ nuôi con dưới 12 tháng tuổi được nghỉ 60 phút mỗi ngày trong giờ làm việc để chăm sóc con.

## ĐIỀU 5: QUY TRÌNH TẠM ỨNG VÀ THANH TOÁN CHI PHÍ (CFO & KẾ TOÁN)
1. Chi phí tiếp khách, công tác hoặc mua sắm vật tư phải được duyệt trước bởi Trưởng bộ phận hoặc CFO nếu số tiền vượt quá 10.000.000 VND.
2. Hóa đơn chứng từ thanh toán hợp lệ (Hóa đơn điện tử VAT) phải được nộp lên cổng ERP trước ngày 25 hàng tháng. Kế toán sẽ thanh toán vào đợt lương ngày 05 tháng kế tiếp.

## ĐIỀU 6: BẢO MẬT DỮ LIỆU VÀ QUY TẮC AN TOÀN THÔNG TIN (ZERO-TRUST)
1. Tuyệt đối không sao chép database khách hàng, mã nguồn phần mềm, hoặc tài liệu mật ra thiết bị cá nhân hoặc lưu trữ đám mây không được cấp phép.
2. Mọi truy cập vào máy chủ cơ sở dữ liệu và hệ thống quản trị nội bộ đều phải qua xác thực 2 lớp (2FA) và ghi log kiểm toán bất biến (Audit Log).
3. Vi phạm quy định an toàn thông tin mức độ nghiêm trọng sẽ bị sa thải ngay lập tức và chuyển hồ sơ sang cơ quan pháp luật xử lý.
"""
                _SEED_DOC_PATH.write_text(seed_content, encoding="utf-8")
                self.ingest_file(_SEED_DOC_PATH, category="Chính sách & Quy chế nội bộ")
                logger.info("[EnterpriseRAG] Đã nạp thành công tài liệu quy chế mẫu vào ChromaDB.")
        except Exception as exc:
            logger.error("[EnterpriseRAG] Lỗi khởi tạo seed data: %s", exc)

    def chunk_text(self, text: str, chunk_size: int = 700, chunk_overlap: int = 150) -> List[str]:
        """Chia văn bản thành các đoạn nhỏ có chồng lấn (overlap) bảo toàn ngữ cảnh."""
        chunks: List[str] = []
        clean_text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not clean_text:
            return chunks

        # Tách theo đoạn (paragraphs)
        paragraphs = clean_text.split("\n\n")
        current_chunk = ""

        for p in paragraphs:
            p_strip = p.strip()
            if not p_strip:
                continue

            if len(current_chunk) + len(p_strip) <= chunk_size:
                current_chunk += ("\n\n" if current_chunk else "") + p_strip
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # Bắt đầu chunk mới kèm overlap
                if len(p_strip) > chunk_size:
                    # Cắt thô nếu paragraph quá dài
                    start = 0
                    while start < len(p_strip):
                        end = start + chunk_size
                        chunks.append(p_strip[start:end])
                        start += chunk_size - chunk_overlap
                    current_chunk = ""
                else:
                    current_chunk = p_strip

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def extract_text_from_file(self, file_path: Path) -> str:
        """Trích xuất nội dung văn bản từ các định dạng PDF, Word, Markdown, TXT, JSON."""
        ext = file_path.suffix.lower()
        if ext in (".txt", ".md", ".json", ".csv"):
            return file_path.read_text(encoding="utf-8", errors="ignore")

        elif ext == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(file_path))
                text_parts = [page.extract_text() or "" for page in reader.pages]
                return "\n\n".join(text_parts)
            except Exception as e:
                logger.warning("[EnterpriseRAG] Lỗi trích xuất PDF %s: %s", file_path.name, e)
                return ""

        elif ext in (".docx", ".doc"):
            try:
                import docx
                doc = docx.Document(str(file_path))
                return "\n".join([p.text for p in doc.paragraphs if p.text])
            except Exception as e:
                logger.warning("[EnterpriseRAG] Lỗi trích xuất Word %s: %s", file_path.name, e)
                return ""

        return ""

    def resolve_ingest_path(self, user_supplied: str) -> Tuple[Path, Optional[str]]:
        """
        Kiểm tra an toàn đường dẫn tài liệu do người dùng cung cấp.

        Zero-Trust: `file_path` từ HTTP là dữ liệu không tin cậy. Trước đây
        endpoint /api/v1/enterprise/rag/ingest nhận thẳng chuỗi này và đưa vào
        Path() — bất kỳ tài khoản nào (kể cả viewer) cũng chỉ cần gửi
        "../../../etc/passwd" là đọc được file tùy ý rồi tra cứu ra qua
        rag/query. Đây là đường đọc file tùy ý (path traversal).

        Chỉ chấp nhận file nằm trực tiếp trong thư mục knowledge_docs của dự án.
        Trả về (path_đã_resolve, lỗi) — lỗi là None khi hợp lệ.
        """
        if not user_supplied or not str(user_supplied).strip():
            return Path(""), "Thiếu tham số: file_path"

        raw = str(user_supplied).strip()

        # Chặn sớm các dạng traversal rõ ràng (sau khi URL-decode người dùng
        # có thể đã gửi %2e%2e%2f). Vẫn kiểm tra lại bằng resolve() bên dưới.
        lowered = raw.replace("\\", "/").lower()
        if ".." in lowered:
            return Path(""), "Đường dẫn chứa '..' — không được phép."

        # Chỉ cho phép đường dẫn tương đối nằm trong thư mục tri thức.
        candidate = Path(raw)
        if candidate.is_absolute():
            return Path(""), "Chỉ được phép nạp tài liệu từ thư mục tri thức của hệ thống."

        allowed_root = _DOCS_DIR.resolve()
        try:
            resolved = (allowed_root / candidate).resolve()
        except OSError as exc:
            return Path(""), f"Không phân giải được đường dẫn: {exc}"

        # Kiểm tra chặt: file phải nằm trong allowed_root, và là file thật
        # (không phải symlink trỏ ra ngoài — resolve() đã xử lý việc này).
        if resolved != allowed_root and allowed_root not in resolved.parents:
            return Path(""), (
                f"Đường dẫn nằm ngoài thư mục tri thức được phép "
                f"({allowed_root.name}/). Hãy tải tài liệu lên qua endpoint upload."
            )
        if not resolved.is_file():
            return Path(""), f"Không tìm thấy tệp trong thư mục tri thức: {raw}"

        return resolved, None

    def ingest_file(self, file_path: Path, category: str = "Tài liệu công ty") -> Dict[str, Any]:
        """Nạp tài liệu, băm nhỏ và vector hóa vào ChromaDB."""
        if not file_path.exists():
            return {"status": "error", "message": f"File không tồn tại: {file_path}"}

        text = self.extract_text_from_file(file_path)
        if not text.strip():
            return {"status": "error", "message": f"Không thể đọc nội dung văn bản từ {file_path.name}."}

        chunks = self.chunk_text(text)
        if not chunks:
            return {"status": "error", "message": "Không tìm thấy nội dung hợp lệ để phân đoạn."}

        doc_name = file_path.name
        now_iso = datetime.now().isoformat()

        # Tạo IDs, Metadatas, Documents
        ids = [f"{doc_name}_chunk_{idx}" for idx in range(len(chunks))]
        metadatas = [
            {
                "doc_name": doc_name,
                "chunk_index": idx,
                "total_chunks": len(chunks),
                "category": category,
                "timestamp": now_iso,
            }
            for idx in range(len(chunks))
        ]

        with self._lock:
            # Xóa các chunk cũ của tài liệu này nếu đã từng upload
            try:
                self.collection.delete(where={"doc_name": doc_name})
            except Exception:
                pass

            self.collection.add(
                documents=chunks,
                metadatas=metadatas,
                ids=ids,
            )

        logger.info("[EnterpriseRAG] Đã vector hóa %d đoạn từ '%s'.", len(chunks), doc_name)

        # ── Bước 2: trích xuất đồ thị tri thức (GraphRAG) ──────────────────
        # Phase 57 BƯỚC 3 yêu cầu hệ thống tự rút Thực thể & Mối quan hệ từ
        # tài liệu. Trước đây ingest chỉ vector hóa, còn đồ thị thì hardcode 16
        # triplet — nên nạp "Quy trình xin nghỉ phép.pdf" vào cũng không tạo ra
        # quan hệ nào. Lỗi trích xuất KHÔNG được làm hỏng cả quá trình ingest
        # vector, nên bọc try/except và báo cáo riêng.
        graph_result: Dict[str, Any] = {"status": "not_attempted"}
        try:
            from mateai.application.knowledge.graph_rag import graph_rag
            graph_result = graph_rag.ingest_document(
                text=text, doc_name=doc_name, category=category
            )
            graph_result["status"] = "success"
        except Exception as exc:
            graph_result = {"status": "failed", "error": str(exc)}
            logger.warning("[EnterpriseRAG] Không trích xuất được đồ thị từ '%s': %s", doc_name, exc)

        return {
            "status": "success",
            "document": doc_name,
            "chunks_count": len(chunks),
            "category": category,
            "graph_extraction": graph_result,
            "message": (
                f"Đã nạp và vector hóa thành công {len(chunks)} đoạn từ tài liệu '{doc_name}'."
                + (
                    f" Đồ thị tri thức: +{graph_result.get('triplets_added', 0)} quan hệ."
                    if graph_result.get("status") == "success" else ""
                )
            ),
        }

    def query(
        self,
        question: str,
        top_k: int = 3,
        min_relevance: float = 0.0,
        require_evidence: bool = True,
    ) -> Dict[str, Any]:
        """
        Truy vấn vector, LỌC theo ngưỡng bằng chứng thật.

        Cổng lọc là **bằng chứng từ khoá** (`lexical_evidence`), KHÔNG phải
        cosine similarity. Lý do — ChromaDB mặc định dùng `ONNX MiniLM-L6-v2`,
        một mô hình **tiếng Anh**. Đo thực tế trên kho quy chế tiếng Việt của
        hệ thống: câu hợp lệ "Nhân viên cần làm gì để truy cập database" chỉ
        đạt cosine **0.071**, trong khi câu vô nghĩa "Quy trình cấp bằng lái xe
        máy điện tử cho tàu ngầm" đạt **0.373** — câu hợp lệ nhất lại thấp
        hơn câu bịa nhất. Không tồn tại ngưỡng cosine nào phân biệt được hai
        loại này, nên dùng cosine làm cổng chống ảo giác là dùng một cái cổng
        không khóa.

        Cosine vẫn được giữ lại để **XẾP HẠNG** (`relevance` trong kết quả), vì
        thứ hạng của nó vẫn hợp lý — chỉ là độ lớn không phản ánh mức độ liên
        quan.

        Tham số:
            question       : truy vấn
            top_k          : số kết quả lấy từ Chroma
            min_relevance  : ngưỡng cosine TÙY CHỌN (mặc định 0 = tắt).
                             Giữ tham số để không phá call site cũ, nhưng
                             không nên bật trên corpus tiếng Việt.
            require_evidence: bật/tắt cổng bằng chứng từ khoá.
        """
        if not (question or "").strip():
            return {"question": question, "total_matches": 0, "matches": [], "filtered_out": 0}

        from mateai.application.knowledge import rag_acl
        acl = rag_acl.Filter()
        # Có tài liệu bị hạn chế thì lấy dư, lọc quyền, rồi cắt về top_k — để đoạn bị ẩn không làm hụt kết quả hợp lệ.
        n_fetch = top_k * 5 if acl.active else top_k
        with self._lock:
            results = self.collection.query(
                query_texts=[question],
                n_results=max(1, min(n_fetch, max(1, self.collection.count()))),
            )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if "distances" in results else []

        matches = []
        filtered_out = 0
        for i, text in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            if not acl.allows(meta.get("doc_name", "")):
                acl.hidden += 1               # chỉ đếm — không lộ tên / nội dung
                continue
            if len(matches) >= top_k:
                break
            dist = distances[i] if i < len(distances) else 0.0
            try:
                # Chroma mặc định cosine: distance = 1 - similarity. Nếu
                # collection cấu hình L2 thì distance âm -> kẹp về 0.
                relevance = max(0.0, 1.0 - float(dist))
            except (TypeError, ValueError):
                relevance = 0.0

            if min_relevance > 0 and relevance < min_relevance:
                filtered_out += 1
                continue

            if require_evidence:
                ok, ev = lexical_evidence_gate(question, text)
                if not ok:
                    filtered_out += 1
                    continue
            else:
                ev = {"hits": 0, "coverage": 0.0, "matched": []}

            matches.append({
                "content": text,
                "document": meta.get("doc_name", "Tài liệu nội bộ"),
                "category": meta.get("category", "Chính sách"),
                "distance": dist,
                "relevance": round(relevance, 3),
                "match_coverage": ev.get("coverage", 0.0),
                "matched_terms": ev.get("matched", []),
            })

        return {
            "question": question,
            "total_matches": len(matches),
            "matches": matches,
            "filtered_out": filtered_out,
            "min_relevance": min_relevance,
            "gate": "lexical_evidence" if require_evidence else "none",
            "hidden_by_permission": acl.hidden,
        }

    def get_searchable_corpus(self) -> List[Dict[str, Any]]:
        """
        Toàn bộ văn bản đã nạp, dạng [{content, source}] — nguồn cho BM25.

        ChromaDB không lưu corpus gốc theo dạng thuận tiện để lập chỉ mục BM25,
        nên đọc thẳng từ collection.get(). Chỉ dùng cho hybrid search nên chấp
        nhận chi phí O(toàn bộ tài liệu); nếu tri thức doanh nghiệp lớn lên
        nên chuyển sang lập chỉ mục BM25 riêng và rebuild khi ingest.
        """
        try:
            with self._lock:
                data = self.collection.get()
        except Exception as exc:
            logger.warning("[RAG] Không đọc được corpus cho BM25: %s", exc)
            return []

        from mateai.application.knowledge import rag_acl
        acl = rag_acl.Filter()
        docs = data.get("documents", []) or []
        metas = data.get("metadatas", []) or []
        out: List[Dict[str, Any]] = []
        for i, content in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            if not content:
                continue
            if not acl.allows(meta.get("doc_name", "")):
                continue
            out.append({
                "content": content,
                "source": meta.get("doc_name", "Tài liệu nội bộ"),
                "category": meta.get("category", "Chính sách"),
            })
        return out

    def answer_policy_question(self, question: str) -> Dict[str, Any]:
        """Tra cứu tri thức công ty và trả lời chính xác, 100% không ảo giác theo văn bản pháp lý."""
        # Không truyền min_relevance: xem `query()` để biết vì sao cosine không
        # dùng làm cổng được. Cổng là bằng chứng từ khoá (mặc định bật).
        retrieval = self.query(question, top_k=3)
        matches = retrieval.get("matches", [])
        hidden = int(retrieval.get("hidden_by_permission") or 0)

        if not matches:
            # Trước đây nhánh này gần như không bao giờ chạy vì Chroma luôn trả
            # đủ 3 kết quả. Nay có ngưỡng nên thực sự thành thật từ chối trả lời
            # khi không có căn cứ, kèm đường nạp tài liệu (graceful fallback).
            return {
                "status": "no_evidence",
                "answer": (
                    f"📋 [KHÔNG TÌM THẤY QUY ĐỊNH PHÙ HỢP]\n\n"
                    f"Dạ em đã tra cứu toàn bộ kho quy chế của công ty nhưng không có văn bản nào "
                    f"liên quan tới \"{question}\" (em đã loại bỏ {retrieval.get('filtered_out', 0)} "
                    f"trích đoạn không đủ độ tin cậy). Em không dám tự trả lời vì có thể bịa quy định.\n\n"
                    f"Sếp muốn em mở khung để tải lên quy chế / SOP liên quan không ạ?"
                ),
                "sources": [],
                "retrieved_chunks": [],
                "needs_document": True,
                # Mã hành động cho phía máy (client có thể tự mở khung upload).
                "suggested_action": "upload_document",
                "hidden_by_permission": hidden,
                # Bản tiếng Việt cho người dùng. Trước đây chỉ có mã máy, nên
                # giao diện hiển thị nguyên chuỗi "upload_document" ra cho C.E.O
                # đọc — nhìn như lỗi hệ thống.
                "suggested_action_text": (
                    f"Hãy tải tài liệu quy chế liên quan tới \"{question}\" lên "
                    f"mục Trung Tâm Chỉ Huy (mục Tra Cứu Quy Chế) để em có căn cứ trả lời."
                ),
            }

        context_snippets = []
        sources = set()
        for i, m in enumerate(matches, 1):
            doc = m.get("document", "Quy chế")
            sources.add(doc)
            rel = m.get("relevance")
            rel_txt = f" (độ tin cậy {rel:.0%})" if isinstance(rel, (int, float)) else ""
            context_snippets.append(f"📄 [Trích đoạn #{i} - {doc}{rel_txt}]:\n{m['content']}")

        full_context = "\n\n".join(context_snippets)

        # Trả lời chính xác, chuẩn mực văn bản pháp chế doanh nghiệp
        answer_text = (
            f"📋 CĂN CỨ THEO QUY CHẾ VÀ CHÍNH SÁCH DOANH NGHIỆP:\n\n"
            f"{full_context}\n\n"
            f"💡 Hướng dẫn thực hiện: Cán bộ nhân viên vui lòng nộp hồ sơ / giấy tờ chứng nhận hợp lệ qua Cổng Portal nội bộ hoặc chuyển trực tiếp cho Bộ phận Nhân sự (HR) theo đúng thời hạn nêu trên."
        )

        return {
            "status": "success",
            "question": question,
            "answer": answer_text,
            "sources": list(sources),
            "retrieved_chunks": matches,
            "needs_document": False,
            "hidden_by_permission": hidden,
        }

    def list_documents(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các tài liệu đã được nạp vào ChromaDB."""
        with self._lock:
            data = self.collection.get()

        from mateai.application.knowledge import rag_acl
        acl = rag_acl.Filter()
        metas = data.get("metadatas", [])
        docs_map: Dict[str, Dict[str, Any]] = {}
        for m in metas:
            name = m.get("doc_name")
            if name and acl.allows(name):
                if name not in docs_map:
                    docs_map[name] = {
                        "name": name,
                        "category": m.get("category", "Chung"),
                        "chunks_count": 0,
                        "last_updated": m.get("timestamp", ""),
                        "classification": acl.classification_of(name),
                    }
                docs_map[name]["chunks_count"] += 1

        return list(docs_map.values())


# Khởi tạo singleton instance
rag_engine = EnterpriseRAGEngine()


# ── AI Skill Export ──────────────────────────────────────────────────────────

@export_skill(
    name="query_company_policy",
    description="Tra cứu nội quy, quy trình chuẩn (SOP), chính sách nhân sự, nghỉ phép, nghỉ ốm, thai sản, tạm ứng công tác phí từ Trung tâm Tri thức Doanh nghiệp (Enterprise RAG). Trả lời chính xác 100% theo văn bản pháp lý của công ty.",
    parameters_schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Câu hỏi của nhân viên hoặc CEO (ví dụ: 'Nghỉ ốm cần nộp giấy tờ gì?', 'Quy trình xin nghỉ phép trên 3 ngày như thế nào?').",
            }
        },
        "required": ["question"],
    },
)
def query_company_policy(question: str) -> Dict[str, Any]:
    """Skill tra cứu chính sách công ty qua Enterprise RAG."""
    return rag_engine.answer_policy_question(question=question)
