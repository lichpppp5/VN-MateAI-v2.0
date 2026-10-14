# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/download_queue.py
======================
Phase 64 — Hàng đợi tải file: để AI Ly Ly tự chuẩn bị file mà không cần
người dùng bấm chuột.

Vì sao không đơn giản là trả một link
-------------------------------------
Cách dễ nghĩ nhất là cho tool `prepare_data_source_export` trả một link có
token, rồi bảo người dùng mở. Cách đó hỏng vì hai lý do đã kiểm chứng:

1. Link đó yêu cầu Bearer token mà trình duyệt KHÔNG tự gắn khi bấm link
   thường -> người dùng bấm xong gặp 401.
2. Câu trả lời của AI được lưu vào lịch sử hội thoại và gửi lại cho nhà
   cung cấp LLM ở các lượt sau. Link có token nằm trong đó là rò bí mật ra
   ngoài, dù token có hạn.

Cách làm ở đây
--------------
Tách "việc chuẩn bị file" (AI làm, chạy trên máy chủ) khỏi "việc tải file"
(giao diện làm, bằng phiên đăng nhập sẵn có).

  AI gọi tool -> máy chủ dựng file, cất vào hàng đợi, trả về `job_id`
             -> giao diện thấy job -> tự tải bằng Bearer token của phiên

`job_id` là mã ngẫu nhiên, KHÔNG phải bí mật. Biết nó cũng vô dụng nếu không
có phiên hợp lệ — nên có nó trong lịch sử chat rồi gửi lên LLM cũng không
lộ gì. Đây là ranh giới quan trọng: chỉ những gì vô hại khi lộ mới được đưa
vào ngữ cảnh LLM.

Vòng đời
-------
    new -> pending -> (giao diện lấy file) -> done
                  \-> hết hạn -> bị dọn

Tự dọn
------
Job chỉ sống `TTL_SECONDS` mặc định 15 phút. Không có bước dọn thì mỗi lần
AI chuẩn bị file lại thêm một entry vĩnh viễn trong RAM — và mỗi entry giữ
toàn bộ dữ liệu báo cáo của khách hàng.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Job sống 15 phút. Đủ dài để người dùng đang đọm dở cuộc trò chuyện thì
#: vẫn tải được, đủ ngắn để dữ liệu kinh doanh không nằm lại lâu trong RAM.
TTL_SECONDS = 900

#: Giới hạn số job chờ. Một phiên chat bình thường không tạo hàng chục job;
#: vượt ngưỡng này nghĩa là có thứ gì đó chạy sai, và giữ lại toàn bộ sẽ chỉ
#: tốn RAM chứ không giúp ích.
MAX_PENDING = 20


@dataclass
class DownloadJob:
    """Một file đã dựng sẵn, chờ giao diện lấy."""

    job_id: str
    source_id: str
    source_title: str
    report: str
    format: str
    title: str
    filename: str
    payload: bytes
    created_at: float = field(default_factory=time.time)
    row_count: int = 0
    #: Hạn sống riêng của job này, do hàng đợi truyền vào. Không đọc hằng số
    #: TTL_SECONDS ở đây — làm vậy thì tham số `ttl_seconds` của DownloadQueue
    #: bị bỏ qua âm thầm, và test với TTL ngắn sẽ không bao giờ thấy job hết hạn.
    ttl_seconds: int = TTL_SECONDS

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) > self.expires_at

    def to_client(self) -> Dict[str, Any]:
        """
        Thông tin gửi giao diện. KHÔNG kèm `payload` — đó là dữ liệu báo cáo,
        phải đi qua endpoint tải có xác thực chứ không nằm trong JSON poll.
        """
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "report": self.report,
            "format": self.format,
            "title": self.title,
            "filename": self.filename,
            "row_count": self.row_count,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class DownloadQueue:
    """Hàng đợi job tải, an toàn khi gọi từ nhiều luồng."""

    def __init__(self, ttl_seconds: int = TTL_SECONDS, max_pending: int = MAX_PENDING):
        self._lock = threading.RLock()
        self._jobs: Dict[str, DownloadJob] = {}
        self._ttl = ttl_seconds
        self._max = max_pending

    def _purge(self, now: Optional[float] = None) -> None:
        """Bỏ job quá hạn. Gọi dưới khoá."""
        now = now or time.time()
        expired = [jid for jid, job in self._jobs.items() if job.is_expired(now)]
        for jid in expired:
            self._jobs.pop(jid, None)
        if expired:
            logger.info("[Download] Đã dọn %d job quá hạn", len(expired))

    def put(
        self,
        *,
        source_id: str,
        source_title: str,
        report: str,
        fmt: str,
        title: str,
        filename: str,
        payload: bytes,
        row_count: int = 0,
    ) -> DownloadJob:
        """Dựng file và xếp vào hàng đợi. Trả job để AI dùng `job_id`."""
        with self._lock:
            now = time.time()
            self._purge(now)

            # Bỏ job cũ nhất khi đầy, thay vì từ chối: người dùng đang chờ
            # file vừa tạo, từ chối thì họ không có gì cả.
            if len(self._jobs) >= self._max:
                oldest = min(self._jobs.values(), key=lambda j: j.created_at)
                self._jobs.pop(oldest.job_id, None)
                logger.warning("[Download] Hàng đợi đầy, bỏ job cũ %s", oldest.job_id)

            job = DownloadJob(
                job_id=secrets.token_urlsafe(12),
                source_id=source_id,
                source_title=source_title,
                report=report,
                format=fmt,
                title=title,
                filename=filename,
                payload=payload,
                created_at=now,
                row_count=row_count,
                ttl_seconds=self._ttl,
            )
            self._jobs[job.job_id] = job
            return job

    def peek(self, job_id: str) -> Optional[DownloadJob]:
        """Xem job mà KHÔNG xoá — dùng để giao diện poll xem có việc gì mới."""
        with self._lock:
            self._purge()
            return self._jobs.get(job_id)

    def take(self, job_id: str) -> Optional[DownloadJob]:
        """
        Lấy và XOÁ job — dùng để giao diện tải file thật.

        Xoá ngay lúc lấy nghĩa là một job chỉ tải được một lần. Nếu tải lỗi
        mạng thì mất, nhưng đổi lại không có job nào tồn tại vô thời hạn.
        """
        with self._lock:
            self._purge()
            return self._jobs.pop(job_id, None)

    def pending(self) -> List[Dict[str, Any]]:
        """Danh sách job đang chờ, mới nhất trước."""
        with self._lock:
            self._purge()
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_client() for j in jobs]

    def drop_all(self) -> int:
        """Xoá sạch — dùng khi test hoặc khi đổi phiên đăng nhập."""
        with self._lock:
            n = len(self._jobs)
            self._jobs.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._jobs)


#: Hàng đợi dùng chung cho toàn hệ thống.
download_queue = DownloadQueue()
