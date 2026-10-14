# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/cognitive_memory.py
========================
Phase 37.5: Portable & Scalable ChromaDB Vector Database for Long-term Memory.

Quản lý Trí nhớ dài hạn (Long-term Cognitive Memory) cho VN-MateAI:
  - Vector DB: ChromaDB.
  - Kiến trúc Factory: Chuyển đổi linh hoạt giữa PersistentClient (local) và HttpClient (microservice).
  - Lưu trữ cục bộ dạng thư mục tại ./storage/vector_db (Dễ dàng di chuyển, copy, sao lưu).
  - Embedding Function: FastSemanticEmbeddingFunction (Offline, nhanh, tối ưu cho thuật ngữ IT & tiếng Việt).
  - Các hàm chuẩn hóa CRUD: memorize_solution(), search_past_incidents(), backup_vector_db(), restore_vector_db().
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Base paths
# Thư mục gốc dự án (đúng cả bản đóng gói) — KHÔNG suy từ vị trí file mã nguồn:
# chuyển module mà đường dẫn lệch là máy chủ mở một CSDL rỗng mới.
from mateai.config.loader import settings as _settings  # noqa: E402

PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
DEFAULT_LOCAL_PATH = PROJECT_ROOT / "storage" / "vector_db"
BACKUPS_DIR = PROJECT_ROOT / "storage" / "backups"
COLLECTION_NAME = "incident_knowledge_base"

# Lazy-loaded ChromaDB objects
_chroma_client: Optional[Any] = None
_incident_collection: Optional[Any] = None


from chromadb.api.types import Documents, EmbeddingFunction, Embeddings


# ---------------------------------------------------------------------------
# Offline Fast Semantic Embedding Function
# ---------------------------------------------------------------------------

class FastSemanticEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Bộ nhúng vector ngữ nghĩa tốc độ cao (0ms, 100% offline, zero-dependency download).
    Kết hợp token words, word-bigrams và character n-grams (3-grams, 4-grams)
    với hàm băm SHA-256 chiếu lên không gian 256 chiều chuẩn hóa L2.
    Đặc biệt nhạy với tên dịch vụ, cổng mạng, mã lỗi (PostgreSQL, 5432, OOM, timeout, 502...).
    """

    def __init__(self, dim: int = 256) -> None:
        super().__init__()
        self.dim = dim

    def name(self) -> str:
        return "fast_semantic_embedding_v1"

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: List[List[float]] = []
        for text in input:
            if not text or not text.strip():
                embeddings.append([0.0] * self.dim)
                continue

            cleaned = text.lower().strip()
            vec = [0.0] * self.dim

            # 1. Trích xuất words
            words = re.findall(r"[\w\.\-]+", cleaned)
            tokens: List[str] = list(words)

            # 2. Trích xuất word bigrams
            if len(words) >= 2:
                for i in range(len(words) - 1):
                    tokens.append(f"{words[i]}_{words[i+1]}")

            # 3. Trích xuất character 3-grams và 4-grams (bắt các từ viết tắt / biến thể)
            for w in words:
                w_clean = re.sub(r"[^\w]", "", w)
                if len(w_clean) >= 3:
                    for i in range(len(w_clean) - 2):
                        tokens.append(w_clean[i:i+3])
                if len(w_clean) >= 4:
                    for i in range(len(w_clean) - 3):
                        tokens.append(w_clean[i:i+4])

            # 4. Chiếu hash lên không gian vector
            for token in tokens:
                h = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if ((h >> 16) % 2 == 0) else -1.0
                weight = 1.2 if len(token) > 4 else 0.6
                vec[idx] += sign * weight

            # 5. L2 Normalization
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            embeddings.append([round(x / norm, 6) for x in vec])

        return embeddings


# ---------------------------------------------------------------------------
# BƯỚC 1 & 2: FACTORY PATTERN KẾT NỐI CHROMADB
# ---------------------------------------------------------------------------

#: Hạn dùng mặc định của một bản ghi trí nhớ (prompt cuối §59).
MEMORY_TTL_DAYS = 365


def _get_config_dict() -> dict:
    """Tải cấu hình memory_db từ config_loader hoặc trực tiếp từ config.json."""
    try:
        from mateai.config.loader import settings
        mem_cfg = getattr(settings, "memory_db", None)
        if mem_cfg:
            if hasattr(mem_cfg, "model_dump"):
                return {"memory_db": mem_cfg.model_dump()}
            elif hasattr(mem_cfg, "dict"):
                return {"memory_db": mem_cfg.dict()}
            elif isinstance(mem_cfg, dict):
                return {"memory_db": mem_cfg}
    except Exception:
        pass

    from mateai.config.loader import read_raw_config
    data = read_raw_config()
    if "memory_db" in data:
        return data

    return {
        "memory_db": {
            "mode": "local",
            "local_path": str(DEFAULT_LOCAL_PATH),
            "microservice_host": "http://localhost:8000",
            "microservice_port": 8000,
        }
    }


def get_vector_db_client(config: Optional[dict] = None) -> Any:
    """
    Pattern Factory: Khởi tạo kết nối ChromaDB tự động theo config.json:
      - mode == "local": chromadb.PersistentClient lưu vào thư mục cục bộ ./storage/vector_db
      - mode == "microservice": chromadb.HttpClient kết nối tới microservice Chroma riêng biệt
    """
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    import chromadb
    from chromadb.config import Settings

    if config is None:
        config = _get_config_dict()

    mem_cfg = config.get("memory_db", {})
    mode = str(mem_cfg.get("mode", "local")).lower().strip()

    if mode == "local":
        raw_path = mem_cfg.get("local_path", "./storage/vector_db")
        if raw_path.startswith("./") or not Path(raw_path).is_absolute():
            resolved_path = PROJECT_ROOT / raw_path.lstrip("./")
        else:
            resolved_path = Path(raw_path)

        resolved_path.mkdir(parents=True, exist_ok=True)
        logger.info("[CognitiveMemory] Khởi tạo ChromaDB PersistentClient tại: %s", resolved_path)
        _chroma_client = chromadb.PersistentClient(
            path=str(resolved_path),
            settings=Settings(anonymized_telemetry=False),
        )
    else:
        # Chế độ Microservice tương lai
        raw_host = mem_cfg.get("microservice_host", "localhost")
        port = int(mem_cfg.get("microservice_port", 8000))
        is_ssl = "https://" in raw_host
        clean_host = raw_host.replace("http://", "").replace("https://", "").split(":")[0].strip()

        logger.info(
            "[CognitiveMemory] Khởi tạo ChromaDB HttpClient tới Microservice: host=%s, port=%d, ssl=%s",
            clean_host, port, is_ssl,
        )
        # pip-audit 2026-10-06: Chroma SERVER có lỗ hổng chưa vá (CVE-2026-45829 chạy mã từ xa không cần
        # đăng nhập, CVE-2026-45830/45831 vượt tenant). Chế độ nhúng (local) không mở cổng nên không bị.
        logger.warning("[CognitiveMemory] Chroma server (%s:%d) có lỗ hổng CHƯA VÁ CVE-2026-45829 / 45830 / 45831 "
                       "— chỉ chạy trong mạng nội bộ kín, chặn mọi truy cập ngoài; ưu tiên chế độ local.",
                       clean_host, port)
        _chroma_client = chromadb.HttpClient(
            host=clean_host,
            port=port,
            ssl=is_ssl,
            settings=Settings(anonymized_telemetry=False),
        )

    return _chroma_client


def get_collection(name: str = COLLECTION_NAME) -> Any:
    """Lấy hoặc tạo Collection trí nhớ sự cố hệ thống với FastSemanticEmbeddingFunction."""
    global _incident_collection
    if _incident_collection is not None:
        return _incident_collection

    client = get_vector_db_client()
    try:
        _incident_collection = client.get_or_create_collection(
            name=name,
            embedding_function=FastSemanticEmbeddingFunction(dim=256),
            metadata={"description": "VN-MateAI Autonomous Incident & RPA Knowledge Base"},
        )
        logger.info("[CognitiveMemory] Đã mở collection '%s' (records=%d)", name, _incident_collection.count())
    except Exception as exc:
        logger.error("[CognitiveMemory] Lỗi mở collection '%s': %s", name, exc)
        raise

    return _incident_collection


# ---------------------------------------------------------------------------
# BƯỚC 3: CÁC HÀM TƯƠNG TÁC (CRUD)
# ---------------------------------------------------------------------------

def memorize_solution(
    error_signature: str,
    root_cause: str,
    script: str,
    target_client: str = "master",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Ghi nhớ giải pháp xử lý sự cố vào Vector DB.
    ChromaDB tự động nhúng (embed) văn bản thành Vector dưới nền.

    Args:
        error_signature: Dấu hiệu lỗi, thông báo exception, log stacktrace.
        root_cause: Nguyên nhân gốc rễ phát hiện bởi LLM.
        script: Mã lệnh / script Python / PowerShell khắc phục sự cố.
        target_client: Thiết bị áp dụng (master/client).
        metadata: Thông tin bổ sung (nếu có).

    Returns:
        Mã ID định danh của giải pháp trong Vector DB.
    """
    col = get_collection()

    doc_id = f"inc_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    timestamp_str = datetime.utcnow().isoformat()

    # Xây dựng document hoàn chỉnh cho semantic embedding
    doc_text = (
        f"DẤU HIỆU SỰ CỐ:\n{error_signature.strip()}\n\n"
        f"NGUYÊN NHÂN GỐC RỄ:\n{root_cause.strip()}\n\n"
        f"KỊCH BẢN KHẮC PHỤC:\n{script.strip()}"
    )

    meta: Dict[str, Any] = {
        "error_signature": error_signature[:300].strip(),
        "root_cause": root_cause[:300].strip(),
        "script": script[:500].strip(),
        "target_client": target_client,
        "timestamp": timestamp_str,
    }
    if metadata and isinstance(metadata, dict):
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[str(k)] = v
    # Độ tin cậy (prompt cuối §59): mặc định CHƯA xác minh, tin cậy thấp, có hạn dùng.
    meta.setdefault("verified", False)
    meta.setdefault("confidence", 1.0 if meta["verified"] else 0.5)
    meta.setdefault("scope", target_client or "global")
    meta.setdefault("source", "unknown")
    meta.setdefault("created_by", "unknown")
    meta.setdefault("expires_at", (datetime.utcnow() + timedelta(days=MEMORY_TTL_DAYS)).isoformat())

    col.add(
        documents=[doc_text],
        metadatas=[meta],
        ids=[doc_id],
    )

    logger.info(
        "[CognitiveMemory] Đã ghi nhớ giải pháp '%s' vào Vector DB (sig='%s'...).",
        doc_id, error_signature[:50],
    )
    return doc_id


def verify_solution(doc_id: str, verified_by: str) -> bool:
    """Admin xác minh một bản ghi: verified=True, confidence=1.0, ghi người xác minh."""
    col = get_collection()
    got = col.get(ids=[doc_id])
    metas = got.get("metadatas") or []
    if not metas:
        return False
    meta = dict(metas[0] or {})
    meta.update({"verified": True, "confidence": 1.0, "verified_by": verified_by,
                 "verified_at": datetime.utcnow().isoformat()})
    col.update(ids=[doc_id], metadatas=[meta])
    return True


def search_past_incidents(
    error_log_snippet: str,
    n_results: int = 3,
) -> List[Dict[str, Any]]:
    """
    Tìm kiếm ngữ nghĩa (Semantic Search) trong kho tri thức sự cố.
    Trả về top n_results giải pháp tương đồng nhất.

    Args:
        error_log_snippet: Đoạn log lỗi cần đối chiếu.
        n_results: Số lượng kết quả gần nhất cần lấy (mặc định 3).

    Returns:
        Danh sách các bản ghi gồm: id, document, metadata, distance, similarity_score.
    """
    col = get_collection()
    count = col.count()
    if count == 0:
        logger.info("[CognitiveMemory] Kho tri thức hiện đang rỗng (0 bản ghi).")
        return []

    fetch_n = min(n_results, count)
    try:
        results = col.query(
            query_texts=[error_log_snippet.strip()],
            n_results=fetch_n,
        )
    except Exception as exc:
        logger.error("[CognitiveMemory] Lỗi semantic search: %s", exc)
        return []

    normalized_results: List[Dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0] if "distances" in results and results["distances"] else []

    now_iso = datetime.utcnow().isoformat()
    for i in range(len(ids)):
        meta_i = metas[i] if i < len(metas) else {}
        # Hết hạn -> không dùng nữa (§59). Bản ghi cũ không có expires_at: giữ, đánh dấu legacy.
        if meta_i.get("expires_at") and str(meta_i["expires_at"]) < now_iso:
            continue
        dist = distances[i] if i < len(distances) else 0.0
        # Similarity score: khoảng cách càng nhỏ thì độ tương đồng càng cao
        sim = round(max(0.0, 1.0 / (1.0 + float(dist))), 4) if dist is not None else 1.0

        item = {
            "id": ids[i],
            "document": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
            "distance": round(float(dist), 4) if dist is not None else None,
            "similarity_score": sim,
            # Nhãn cho người / AI đọc: chưa xác minh là THAM KHẢO, không phải sự thật (§60).
            "trust": ("verified" if meta_i.get("verified") is True
                      else "unverified" if "verified" in meta_i else "legacy_unverified"),
        }
        normalized_results.append(item)

    logger.info(
        "[CognitiveMemory] Semantic query cho '%s...' tìm thấy %d kết quả (Top sim: %s).",
        error_log_snippet[:40], len(normalized_results),
        normalized_results[0]["similarity_score"] if normalized_results else "N/A",
    )
    return normalized_results


def list_recent_incidents(limit: int = 20) -> List[Dict[str, Any]]:
    """Liệt kê các sự cố đã ghi nhớ gần đây."""
    col = get_collection()
    count = col.count()
    if count == 0:
        return []

    fetch_n = min(limit, count)
    try:
        data = col.get(limit=fetch_n, include=["documents", "metadatas"])
        res = []
        ids = data.get("ids", [])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        for i in range(len(ids)):
            res.append({
                "id": ids[i],
                "document": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
            })
        return res
    except Exception as exc:
        logger.error("[CognitiveMemory] Lỗi list_recent_incidents: %s", exc)
        return []


def delete_incident(incident_id: str) -> bool:
    """Xóa một giải pháp khỏi Vector DB theo ID."""
    col = get_collection()
    try:
        col.delete(ids=[incident_id])
        logger.info("[CognitiveMemory] Đã xóa sự cố '%s' khỏi Vector DB.", incident_id)
        return True
    except Exception as exc:
        logger.error("[CognitiveMemory] Lỗi xóa sự cố '%s': %s", incident_id, exc)
        return False


def get_memory_stats() -> Dict[str, Any]:
    """Trả về số liệu thống kê chi tiết của Vector DB."""
    cfg = _get_config_dict()
    mem_cfg = cfg.get("memory_db", {})
    mode = mem_cfg.get("mode", "local")

    col = get_collection()
    total_records = col.count()

    size_mb = 0.0
    local_dir_path = ""
    if mode == "local":
        raw_path = mem_cfg.get("local_path", "./storage/vector_db")
        if raw_path.startswith("./") or not Path(raw_path).is_absolute():
            resolved = PROJECT_ROOT / raw_path.lstrip("./")
        else:
            resolved = Path(raw_path)
        local_dir_path = str(resolved)
        if resolved.exists():
            total_bytes = sum(f.stat().st_size for f in resolved.rglob("*") if f.is_file())
            size_mb = round(total_bytes / (1024 * 1024), 2)

    return {
        "status": "ready",
        "mode": mode,
        "collection_name": COLLECTION_NAME,
        "total_records": total_records,
        "storage_path": local_dir_path or mem_cfg.get("microservice_host", ""),
        "size_mb": size_mb,
    }


# ---------------------------------------------------------------------------
# BƯỚC 4: TÍCH HỢP TÍNH NĂNG BACKUP / DI CHUYỂN DỮ LIỆU
# ---------------------------------------------------------------------------

def backup_vector_db(dest_dir: Optional[str] = None) -> str:
    """
    Nén (Zip) toàn bộ thư mục ./storage/vector_db để sao lưu hoặc di chuyển sang máy chủ mới.
    Vì ChromaDB ở chế độ Persistent lưu toàn bộ index & metadata vào file SQLite/Parquet tĩnh,
    khi giải nén lại ở máy mới, Trí nhớ dài hạn của AI được khôi phục nguyên vẹn 100%.

    Args:
        dest_dir: Thư mục đích lưu file zip (mặc định ./storage/backups/).

    Returns:
        Đường dẫn tuyệt đối của tệp zip backup đã tạo.
    """
    cfg = _get_config_dict()
    raw_path = cfg.get("memory_db", {}).get("local_path", "./storage/vector_db")
    if raw_path.startswith("./") or not Path(raw_path).is_absolute():
        src_path = PROJECT_ROOT / raw_path.lstrip("./")
    else:
        src_path = Path(raw_path)

    if not src_path.exists():
        src_path.mkdir(parents=True, exist_ok=True)

    backup_folder = Path(dest_dir) if dest_dir else BACKUPS_DIR
    backup_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"vector_db_backup_{timestamp}.zip"
    zip_filepath = backup_folder / zip_filename

    logger.info("[CognitiveMemory] Bắt đầu nén sao lưu Vector DB từ %s -> %s", src_path, zip_filepath)

    with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(src_path):
            for file in files:
                abs_file = Path(root) / file
                rel_path = abs_file.relative_to(src_path)
                zipf.write(abs_file, arcname=str(rel_path))

    zip_size_mb = round(zip_filepath.stat().st_size / (1024 * 1024), 2)
    logger.info("[CognitiveMemory] ✅ Sao lưu Vector DB thành công: %s (%s MB)", zip_filename, zip_size_mb)
    return str(zip_filepath)


def restore_vector_db(zip_filepath: str) -> bool:
    """
    Khôi phục Vector DB từ file zip sao lưu.
    """
    global _chroma_client, _incident_collection

    zip_path = Path(zip_filepath)
    if not zip_path.is_file():
        logger.error("[CognitiveMemory] Tệp zip không tồn tại: %s", zip_filepath)
        return False

    cfg = _get_config_dict()
    raw_path = cfg.get("memory_db", {}).get("local_path", "./storage/vector_db")
    if raw_path.startswith("./") or not Path(raw_path).is_absolute():
        dest_path = PROJECT_ROOT / raw_path.lstrip("./")
    else:
        dest_path = Path(raw_path)

    # Đóng kết nối cũ
    _chroma_client = None
    _incident_collection = None

    dest_path.mkdir(parents=True, exist_ok=True)

    logger.info("[CognitiveMemory] Bắt đầu giải nén phục hồi Vector DB vào: %s", dest_path)
    with zipfile.ZipFile(zip_path, "r") as zipf:
        zipf.extractall(dest_path)

    # Khởi động lại client
    get_collection()
    logger.info("[CognitiveMemory] ✅ Phục hồi Vector DB hoàn tất từ: %s", zip_path.name)
    return True
