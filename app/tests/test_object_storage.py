# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_object_storage.py
============================
Prompt cuối §68 (object storage) + §136 (bản sao lưu ngoài máy).

Một giao diện `object_storage` (put / get / exists / delete / list) cho hai backend chạy CÙNG bộ
test: `local` (thư mục) và `s3` (S3 thật — SeaweedFS từ `.infra/seaweedfs` hoặc
VNMATEAI_TEST_S3_ENDPOINT; không có thì bỏ qua, KHÔNG giả lập).
Tài liệu tri thức tải lên được lưu bền vào object storage; bản sao lưu đẩy lên được.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEED = ROOT / ".infra" / "seaweedfs" / "weed.exe"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_pair() -> int:
    """SeaweedFS mở thêm cổng gRPC = cổng + 10000 -> chọn cổng thấp, kiểm tra cả hai còn trống."""
    import random
    for _ in range(200):
        p = random.randint(20000, 40000)
        try:
            for q in (p, p + 10000):
                with socket.socket() as s:
                    s.bind(("127.0.0.1", q))
            return p
        except OSError:
            continue
    raise RuntimeError("không tìm được cặp cổng trống")


def _wait(port: int, timeout: float = 40.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def s3_endpoint(tmp_path_factory):
    ep = os.environ.get("VNMATEAI_TEST_S3_ENDPOINT")
    if ep:
        yield ep
        return
    if not WEED.exists():
        pytest.skip("Không có S3 thật (.infra/seaweedfs hoặc VNMATEAI_TEST_S3_ENDPOINT) — chạy scripts/dev_infra.py fetch")
    d = tmp_path_factory.mktemp("weed")
    ports = {k: _port_pair() for k in ("s3", "master", "volume", "filer")}
    proc = subprocess.Popen([str(WEED), "server", f"-dir={d}", "-ip=127.0.0.1", "-s3",
                             f"-s3.port={ports['s3']}", f"-master.port={ports['master']}",
                             f"-volume.port={ports['volume']}", f"-filer.port={ports['filer']}",
                             # cổng phụ mặc định cố định (9101…) -> tắt để chạy song song instance khác
                             "-s3.port.lance=0", "-s3.port.iceberg=0"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait(ports["s3"]):
            pytest.skip("SeaweedFS không lên kịp")
        time.sleep(2)
        yield f"127.0.0.1:{ports['s3']}"
    finally:
        proc.terminate()
        proc.wait(timeout=20)


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    from mateai.infrastructure.files import object_storage as osm
    if request.param == "local":
        return osm.LocalObjectStore(tmp_path / "objects")
    ep = request.getfixturevalue("s3_endpoint")
    return osm.S3ObjectStore(endpoint=ep, bucket=f"t-{uuid.uuid4().hex[:10]}", access_key_id="test",
                             secret_access_key="test", secure=False)


def test_put_get_list_delete(store):
    store.put("knowledge/a b.pdf", b"%PDF-1 noi dung", content_type="application/pdf")
    store.put("knowledge/b.txt", "xin chào".encode("utf-8"))
    store.put("backups/x.zip", b"zip")
    assert store.get("knowledge/a b.pdf") == b"%PDF-1 noi dung"
    assert store.exists("knowledge/b.txt") and not store.exists("knowledge/khong.txt")
    assert sorted(store.list("knowledge/")) == ["knowledge/a b.pdf", "knowledge/b.txt"]
    store.delete("knowledge/b.txt")
    assert sorted(store.list("knowledge/")) == ["knowledge/a b.pdf"]
    with pytest.raises(KeyError):
        store.get("knowledge/b.txt")


def test_keys_cannot_escape_the_store(store):
    for bad in ("../etc/passwd", "/abs/path", "a/../../b", ""):
        with pytest.raises(ValueError):
            store.put(bad, b"x")


def test_knowledge_upload_is_persisted_to_object_storage(tmp_path, monkeypatch):
    from mateai.application.knowledge import rag_engine
    from mateai.infrastructure.files import object_storage as osm
    st = osm.LocalObjectStore(tmp_path / "objects")
    monkeypatch.setattr(osm, "_STORE", st)
    monkeypatch.setitem(rag_engine.__dict__, "_DOCS_DIR", tmp_path / "docs")
    p = rag_engine.save_upload("Quy định nghỉ phép.txt", "Nghỉ phép 12 ngày/năm".encode("utf-8"))
    assert p.exists()
    keys = st.list("knowledge/")
    assert keys == [f"knowledge/{p.name}"] and st.get(keys[0]) == p.read_bytes()


def test_backup_archive_can_be_pushed_and_fetched(tmp_path, monkeypatch, store):
    import importlib.util
    spec = importlib.util.spec_from_file_location("backup_mod", ROOT / "scripts" / "backup.py")
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    archive = tmp_path / "vnmate-backup-test.zip"
    archive.write_bytes(b"PK\x03\x04 du lieu sao luu")
    key = backup.push(archive, store=store)
    assert key == "backups/vnmate-backup-test.zip"
    out = backup.fetch(key, tmp_path / "back", store=store)
    assert out.read_bytes() == archive.read_bytes()
