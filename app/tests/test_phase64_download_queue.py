# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase64_download_queue.py
====================================
Kiểm thử Phase 64 — AI tự dựng file và giao diện tự tải.

Trọng tâm là những điều sai thì người dùng mất file hoặc rò dữ liệu:
  - job phải chỉ tải được MỘT lần, lần hai phải hết hạn
  - job quá hạn phải tự biến mất, không nằm lại giữ dữ liệu khách hàng
  - `job_id` phải vô dụng nếu lọt vào lịch sử chat và được gửi lên LLM
  - phản hồi poll KHÔNG được chứa dữ liệu báo cáo
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ══ 1. Hàng đợi ═══════════════════════════════════════════════════════════
section("Hàng đợi tải")
from mateai.infrastructure.files.download_queue import DownloadQueue, TTL_SECONDS  # noqa: E402

q = DownloadQueue(ttl_seconds=60, max_pending=3)
sample = b"\xef\xbb\xbfma_hang,ten\nSP001,Ghe xoay"

job = q.put(source_id="erp-abc", source_title="ERP ABC", report="tồn kho", fmt="csv",
            title="ERP ABC", filename="erp-abc-20260928.csv", payload=sample, row_count=2)
check("put trả job có job_id", bool(job.job_id))
check("job_id đủ dài để không đoán", len(job.job_id) >= 16, str(len(job.job_id)))
check("peek không xoá job", q.peek(job.job_id) is not None)
check("job vẫn còn sau peek", len(q) == 1)

taken = q.take(job.job_id)
check("take trả đúng payload", taken is not None and taken.payload == sample)
check("take XOÁ job khỏi hàng đợi", len(q) == 0, str(len(q)))
check("lần hai -> không có gì (một job chỉ tải 1 lần)", q.take(job.job_id) is None)

section("job_id không phải bí mật")
q2 = DownloadQueue(ttl_seconds=60)
ids = {q2.put(source_id="s", source_title="t", report="r", fmt="csv", title="t",
              filename="f.csv", payload=b"x").job_id for _ in range(50)}
check("50 job -> 50 mã khác nhau", len(ids) == 50, str(len(ids)))
# Đừng khẳng định `job_id` không chứa một chuỗi con cụ thể: `put()` dùng
# `secrets.token_urlsafe(12)` nên chuỗi là base64url — "erp" hoàn toàn CÓ THỂ
# xuất hiện tình cờ (đo được ~0,04% mỗi mã, tức ~2% mỗi lần chạy với 50 mã).
# Test hỏng ngẫu nhiên 1/50 lần làm cả bộ test mất uy tín.
# Kiểm đúng ý định: mã sinh ra KHÔNG phải hàm của dữ liệu đầu vào.
q2_same_input = DownloadQueue(ttl_seconds=60)
ids_same_input = {
    q2_same_input.put(source_id="s", source_title="t", report="r", fmt="csv",
                      title="t", filename="f.csv", payload=b"x").job_id
    for _ in range(50)
}
check(
    "cùng đầu vào vẫn cho mã khác nhau (không suy ra từ tên nguồn)",
    not (ids & ids_same_input),
    "nếu mã bắt nguồn từ đầu vào thì hai lần put cùng dữ liệu phải trùng",
)
check("job_id không phải số đoán được", all(not i.isdigit() for i in ids))

section("Hết hạn")
q3 = DownloadQueue(ttl_seconds=1)
j3 = q3.put(source_id="s", source_title="t", report="r", fmt="csv",
            title="t", filename="f.csv", payload=b"du lieu")
check("còn trong hàng đợi khi mới tạo", q3.peek(j3.job_id) is not None)
time.sleep(1.2)
check("hết hạn -> peek trả None", q3.peek(j3.job_id) is None)
check("hết hạn -> không còn trong pending", q3.pending() == [])
check("hết hạn -> take trả None", q3.take(j3.job_id) is None)
check("hàng đợi tự dọn", len(q3) == 0, str(len(q3)))

section("Giới hạn số job chờ")
q4 = DownloadQueue(ttl_seconds=600, max_pending=3)
made = [q4.put(source_id="s", source_title="t", report="r", fmt="csv",
               title="t", filename=f"f{i}.csv", payload=b"x").job_id for i in range(5)]
check("không vượt quá giới hạn", len(q4) <= 3, str(len(q4)))
check("giữ job MỚI NHẤT (bỏ job cũ, không từ chối)", q4.peek(made[-1]) is not None)
check("job cũ đã bị bỏ", q4.peek(made[0]) is None)

section("Thread safety")
q5 = DownloadQueue(ttl_seconds=600, max_pending=200)
errors: list = []


def _writer(i: int) -> None:
    try:
        q5.put(source_id="s", source_title="t", report="r", fmt="csv",
               title="t", filename=f"f{i}.csv", payload=b"x" * (i + 1))
    except Exception as exc:  # pragma: no cover
        errors.append(str(exc))


import threading  # noqa: E402

threads = [threading.Thread(target=_writer, args=(i,)) for i in range(30)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("30 luồng ghi song song không lỗi", not errors, str(errors[:2]))
check("không mất job nào", len(q5) == 30, str(len(q5)))


# ══ 2. Metadata gửi giao diện ═════════════════════════════════════════════
section("Phản hồi poll không chứa dữ liệu báo cáo")
q6 = DownloadQueue(ttl_seconds=600)
secret_rows = b"SP001,Don gia bi mat:1250000"
j6 = q6.put(source_id="erp", source_title="ERP", report="r", fmt="csv",
            title="ERP", filename="erp.csv", payload=secret_rows, row_count=1)
client = j6.to_client()
blob = json.dumps(client, ensure_ascii=False)
check("không kèm payload (dữ liệu dòng)", "payload" not in client, str(list(client)))
check("không lộ nội dung dòng dữ liệu", "1250000" not in blob, blob[:160])
check("vẫn đủ thông tin để giao diện tải",
      all(k in client for k in ("job_id", "filename", "format", "row_count", "expires_in")),
      str(list(client)))
check("báo thời hạn còn lại", 0 < client["expires_in"] <= TTL_SECONDS, str(client.get("expires_in")))


# ══ 3. Tool AI ═══════════════════════════════════════════════════════════
section("Tool prepare_data_source_export dựng file thật")
from mateai.infrastructure.connectors import custom_registry  # noqa: E402
from mateai.infrastructure.files.download_queue import download_queue  # noqa: E402

import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="vnmate-dl-")
custom_registry.STORE_PATH = Path(_TMP) / "data_sources.json"

from skills.data_source_tools import prepare_data_source_export  # noqa: E402


class _FakeResult:
    def __init__(self, data, success=True, error=None):
        self.data = data
        self.success = success
        self.error = error
        self.latency_ms = 1.0
        self.metadata = {}
        self.source = "test"


# Nguồn không tồn tại
r = asyncio.run(prepare_data_source_export(source_id="khong-co"))
check("nguồn không tồn tại -> lỗi", r.get("success") is False)
check("nguồn không tồn tại -> có gợi ý", "list_data_sources" in (r.get("hint") or ""))

# Chưa có app thật -> lời gọi ra ngoài thất bại, phải báo lỗi chứ không hứa file
custom_registry.upsert_source("erp-test", {
    "id": "erp-test", "title": "ERP Test", "base_url": "http://127.0.0.1:1/api",
    "default_path": "/r", "auth_type": "none", "timeout_seconds": 1,
})
r2 = asyncio.run(prepare_data_source_export(source_id="erp-test"))
check("app không chạy -> lỗi, KHÔNG báo sẵn sàng", r2.get("success") is False, str(r2)[:120])
check("app không chạy -> không đẩy job", "job_id" not in r2, str(list(r2)))

r3 = asyncio.run(prepare_data_source_export(source_id="erp-test", format="pdf"))
check("format sai -> báo lỗi rõ", "xlsx" in (r3.get("error") or ""), str(r3.get("error")))

section("Kết quả trả về không chứa bí mật")
blob3 = json.dumps(r2, ensure_ascii=False)
check("không lộ khoá", "auth_value" not in blob3)
check("không lộ base_url nội bộ", "127.0.0.1:1" not in blob3, blob3[:160])


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
