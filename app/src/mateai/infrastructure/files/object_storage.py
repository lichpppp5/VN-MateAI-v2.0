# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/files/object_storage.py
=============================================
Lưu trữ đối tượng (prompt cuối §68): tệp nhị phân bền (tài liệu tri thức, bản sao lưu) không
phụ thuộc ổ đĩa của MỘT máy ứng dụng.

  object_storage.backend = "local" (mặc định)  -> thư mục storage/objects
                         = "s3"                -> mọi dịch vụ S3 (AWS S3, OCI, SeaweedFS, Ceph…)

Cấu hình khối `object_storage` trong config.json: endpoint, bucket, access_key_id,
secret_access_key (bí mật — tự che / mã hoá như mọi khoá khác), secure, region.
Khoá (key) là đường dẫn tương đối, không được thoát ra ngoài kho ("..", đường tuyệt đối).
"""
from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

Data = Union[bytes, bytearray]


def _check_key(key: str) -> str:
    k = str(key or "").replace("\\", "/").strip()
    parts = k.split("/")
    if not k or k.startswith("/") or ":" in parts[0] or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Khoá đối tượng không hợp lệ: {key!r}")
    return k


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put(self, key: str, data: Data, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(bytes(data))
        tmp.replace(p)                      # ghi nguyên tử
        return _check_key(key)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise KeyError(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str = "") -> List[str]:
        if not self.root.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and not p.name.endswith(".part")
                      and p.relative_to(self.root).as_posix().startswith(prefix))


class S3ObjectStore:
    def __init__(self, endpoint: str, bucket: str, access_key_id: str, secret_access_key: str,
                 secure: bool = True, region: Optional[str] = None) -> None:
        from minio import Minio
        self.bucket = bucket
        self._c = Minio(endpoint, access_key=access_key_id, secret_key=secret_access_key,
                        secure=secure, region=region or None)
        self._ready = False
        self._lock = threading.Lock()

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        with self._lock:
            if not self._c.bucket_exists(self.bucket):
                self._c.make_bucket(self.bucket)
            self._ready = True

    def put(self, key: str, data: Data, content_type: str = "application/octet-stream") -> str:
        k = _check_key(key)
        self._ensure_bucket()
        self._c.put_object(self.bucket, k, io.BytesIO(bytes(data)), len(data), content_type=content_type)
        return k

    def get(self, key: str) -> bytes:
        from minio.error import S3Error
        k = _check_key(key)
        self._ensure_bucket()
        try:
            resp = self._c.get_object(self.bucket, k)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                raise KeyError(key) from exc
            raise
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, key: str) -> bool:
        from minio.error import S3Error
        self._ensure_bucket()
        try:
            self._c.stat_object(self.bucket, _check_key(key))
            return True
        except S3Error:
            return False

    def delete(self, key: str) -> None:
        self._ensure_bucket()
        self._c.remove_object(self.bucket, _check_key(key))

    def list(self, prefix: str = "") -> List[str]:
        self._ensure_bucket()
        return sorted(o.object_name for o in self._c.list_objects(self.bucket, prefix=prefix, recursive=True))


_STORE: Optional[object] = None
_STORE_LOCK = threading.Lock()


def object_store():
    """Kho đối tượng theo cấu hình (một bản cho cả tiến trình)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            from mateai.config.loader import get_config_section, settings
            cfg = dict(get_config_section("object_storage") or {})
            if str(cfg.get("backend") or "local").lower() == "s3":
                _STORE = S3ObjectStore(endpoint=str(cfg.get("endpoint") or ""), bucket=str(cfg.get("bucket") or "vnmateai"),
                                       access_key_id=str(cfg.get("access_key_id") or ""),
                                       secret_access_key=str(cfg.get("secret_access_key") or ""),
                                       secure=bool(cfg.get("secure", True)), region=cfg.get("region"))
                logger.info("[ObjectStorage] S3 %s / bucket %s", cfg.get("endpoint"), cfg.get("bucket"))
            else:
                _STORE = LocalObjectStore(Path(settings.PROJECT_ROOT) / "storage" / "objects")
        return _STORE
