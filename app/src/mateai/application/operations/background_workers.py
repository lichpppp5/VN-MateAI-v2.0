# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/background_workers.py
==========================
Background Task Workers — Phase 60 Enterprise Middleware & Plugin Registry.

Cô lập các tác vụ nặng (external API calls, report generation, data sync)
khỏi Main Thread / Event Loop để không block WebSocket, Streaming Audio, Voice.

Kiến trúc:
  - FastAPI BackgroundTasks cho tác vụ ngắn (fire-and-forget).
  - Dedicated Worker Pool (asyncio TaskGroup) cho tác vụ dài, cần progress tracking.
  - TTS Notification: Phát câu filler ("Em đang tổng hợp báo cáo...") NGAY LẬP TỨC
    qua Audio Cache / ESP32, sau đó worker chạy nền, xong thì TTS kết quả.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


# ================================================================
# Task Models
# ================================================================

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """
    Một tác vụ nền (background job).
    """
    id: str
    name: str                                   # Tên định danh: "generate_monthly_report"
    description: str                            # Mô tả cho user
    func: Callable                              # Async function thực thi
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None
    progress: float = 0.0                       # 0.0 - 1.0
    progress_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "metadata": self.metadata,
        }


# ================================================================
# Background Worker Manager
# ================================================================

class BackgroundWorkerManager:
    """
    Quản lý pool worker nền.
    Sử dụng asyncio TaskGroup (Python 3.11+) hoặc create_task + tracking.
    """

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._tasks: Dict[str, BackgroundTask] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._shutdown = False

    async def start(self) -> None:
        """Khởi tạo semaphore."""
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        logger.info("[BackgroundWorkerManager] Started with max_concurrent=%d", self.max_concurrent)

    async def stop(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: wait for running tasks."""
        self._shutdown = True
        if self._running_tasks:
            logger.info("[BackgroundWorkerManager] Waiting for %d running tasks...", len(self._running_tasks))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._running_tasks.values(), return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("[BackgroundWorkerManager] Shutdown timeout, cancelling remaining tasks")
                for task in self._running_tasks.values():
                    task.cancel()
                await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        logger.info("[BackgroundWorkerManager] Stopped")

    # ------------------------------------------------------------------
    # Submit Task
    # ------------------------------------------------------------------

    async def submit(
        self,
        name: str,
        func: Callable,
        *args,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        tts_filler: Optional[str] = None,
        tts_filler_key: Optional[str] = None,
        notify_on_complete: bool = True,
        **kwargs,
    ) -> str:
        """
        Gửi một tác vụ vào background pool.

        Args:
            name: Tên task (unique key, vd: "monthly_report_2024_01")
            func: Async function để thực thi
            args/kwargs: Tham số cho func
            description: Mô tả user-friendly
            metadata: Metadata bổ sung
            tts_filler: Câu filler TTS phát NGAY khi submit (vd: "Em đang tổng hợp báo cáo...")
            tts_filler_key: Key trong Audio Cache cho filler (ưu tiên hơn tts_filler)
            notify_on_complete: Có phát TTS kết quả khi xong không

        Returns:
            task_id: ID để track progress / get result
        """
        if self._shutdown:
            raise RuntimeError("Worker manager is shutting down")

        task_id = str(uuid.uuid4())[:8]  # Short ID
        if name in self._tasks and self._tasks[name].status == TaskStatus.RUNNING:
            # Deduplicate: nếu task cùng tên đang chạy, trả về task đó
            logger.info("[BackgroundWorkerManager] Task '%s' already running, returning existing", name)
            return self._tasks[name].id

        task = BackgroundTask(
            id=task_id,
            name=name,
            description=description or name,
            func=func,
            args=args,
            kwargs=kwargs,
            metadata=metadata or {},
        )
        task.metadata.update({
            "tts_filler": tts_filler,
            "tts_filler_key": tts_filler_key,
            "notify_on_complete": notify_on_complete,
        })

        async with self._lock:
            self._tasks[name] = task

        # Fire filler TTS immediately (non-blocking)
        if tts_filler_key or tts_filler:
            asyncio.create_task(self._play_filler(task))

        # Schedule execution
        asyncio.create_task(self._run_task(task))

        logger.info("[BackgroundWorkerManager] Submitted task: %s (id=%s)", name, task_id)
        return task_id

    async def _play_filler(self, task: BackgroundTask) -> None:
        """Phát câu filler TTS ngay lập tức qua Audio Cache / VoiceController."""
        try:
            from mateai.interfaces.desktop.voice_controller import voice_controller
            from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes

            filler_key = task.metadata.get("tts_filler_key")
            filler_text = task.metadata.get("tts_filler")

            if filler_key:
                audio_bytes = get_cached_audio_bytes(filler_key)
                if audio_bytes:
                    voice_controller._play_cached_phrase_instant_async(filler_key)
                    return

            if filler_text:
                # Fallback: synthesize on the fly (slow, but still async)
                voice_controller._play_cached_phrase_instant_async(filler_text)

        except Exception as e:
            logger.debug("[BackgroundWorkerManager] Filler TTS failed: %s", e)

    async def _run_task(self, task: BackgroundTask) -> None:
        """Thực thi task trong semaphore."""
        async with self._semaphore:
            if self._shutdown:
                task.status = TaskStatus.CANCELLED
                task.error = "Worker manager shutting down"
                return

            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow()
            from mateai.application.operations.topology_events import emit
            emit("job", stage="start", node="jobs", status="running", trace_id=f"job-{task.id}",
                 detail=task.name)

            async with self._lock:
                self._running_tasks[task.name] = asyncio.current_task()

            try:
                # Execute with progress callback support
                async def progress_callback(progress: float, message: str = "") -> None:
                    task.progress = max(0.0, min(1.0, progress))
                    task.progress_message = message

                # Check if func accepts progress_callback
                import inspect
                sig = inspect.signature(task.func)
                call_kwargs = dict(task.kwargs)
                if "progress_callback" in sig.parameters:
                    call_kwargs["progress_callback"] = progress_callback

                # `submit()` nhận cả hàm sync lẫn async. Trước đây chỉ `await`
                # được nên hàm sync rơi vào `TypeError: object dict can't be used
                # in 'await' expression` và bị ghi nhận FAILED — thất bại âm
                # thầm, dễ chẩn đoán nhầm thành lỗi nghiệp vụ.
                #
                # Hàm sync được đẩy sang thread pool: đó chính là mục đích của
                # worker manager, và là cách duy nhất để tác vụ nặng kiểu
                # `rag_engine.ingest_file()` (parse PDF + chunk + embed) không
                # chặn event loop của server.
                if inspect.iscoroutinefunction(task.func):
                    result = await task.func(*task.args, **call_kwargs)
                else:
                    result = await asyncio.to_thread(
                        functools.partial(task.func, *task.args, **call_kwargs)
                    )

                task.status = TaskStatus.COMPLETED
                task.result = result
                task.progress = 1.0
                task.progress_message = "Hoàn tất"
                logger.info("[BackgroundWorkerManager] Task '%s' completed", task.name)

            except asyncio.CancelledError:
                task.status = TaskStatus.CANCELLED
                task.error = "Cancelled"
                logger.info("[BackgroundWorkerManager] Task '%s' cancelled", task.name)
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                logger.error("[BackgroundWorkerManager] Task '%s' failed: %s", task.name, e, exc_info=True)
            finally:
                task.completed_at = datetime.utcnow()
                emit("job", stage="end", node="jobs", trace_id=f"job-{task.id}",
                     status={TaskStatus.COMPLETED: "ok", TaskStatus.CANCELLED: "cancelled"}.get(task.status, "error"),
                     ms=(task.completed_at - task.started_at).total_seconds() * 1000,
                     detail=task.name + (f": {task.error}" if task.error else ""))
                async with self._lock:
                    self._running_tasks.pop(task.name, None)

                # Notify completion via TTS
                if task.metadata.get("notify_on_complete"):
                    await self._notify_completion(task)

    async def _notify_completion(self, task: BackgroundTask) -> None:
        """Phát TTS kết quả khi task xong."""
        try:
            from mateai.interfaces.desktop.voice_controller import voice_controller

            if task.status == TaskStatus.COMPLETED:
                msg = f"Dạ, {task.description} đã xong. Kết quả đã sẵn sàng trên màn hình."
            else:
                msg = f"Dạ, {task.description} gặp sự cố: {task.error[:100]}"

            voice_controller._play_cached_phrase_instant_async(msg)

            # Also broadcast to HUD/Portal
            from mateai.interfaces.websocket.realtime_hub import broadcast_portal_ui
            try:
                await broadcast_portal_ui("background_task_complete", task.to_dict())
            except Exception:
                pass

        except Exception as e:
            logger.debug("[BackgroundWorkerManager] Completion notify failed: %s", e)

    # ------------------------------------------------------------------
    # Task Query
    # ------------------------------------------------------------------

    def get_task(self, name: str) -> Optional[BackgroundTask]:
        """Lấy task theo name."""
        return self._tasks.get(name)

    def get_task_by_id(self, task_id: str) -> Optional[BackgroundTask]:
        """Lấy task theo ID."""
        for task in self._tasks.values():
            if task.id == task_id:
                return task
        return None

    def list_tasks(self, status: Optional[TaskStatus] = None) -> List[BackgroundTask]:
        """Liệt kê tasks."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def cancel_task(self, name: str) -> bool:
        """Hủy task đang chạy."""
        task = self._tasks.get(name)
        if not task:
            return False
        if task.status != TaskStatus.RUNNING:
            return False

        running_task = self._running_tasks.get(name)
        if running_task:
            running_task.cancel()
            return True
        return False


# ================================================================
# FastAPI BackgroundTasks Integration (Lightweight)
# ================================================================

class FastAPIBackgroundHelper:
    """
    Helper để dùng FastAPI BackgroundTasks cho tác vụ nhẹ, fire-and-forget.
    Không cần tracking phức tạp, chỉ cần chạy xong.
    """

    @staticmethod
    async def run_with_filler(
        background_tasks,  # FastAPI BackgroundTasks
        func: Callable,
        filler_text: str,
        filler_key: Optional[str] = None,
        *args,
        **kwargs,
    ) -> None:
        """
        Chạy func trong background, phát filler TTS ngay lập tức.

        Usage:
            @app.post("/api/report/generate")
            async def generate_report(bg: BackgroundTasks):
                await FastAPIBackgroundHelper.run_with_filler(
                    bg, heavy_report_func,
                    filler_text="Em đang tổng hợp báo cáo, anh chờ một lát nhé",
                    filler_key="filler_report_generating",
                )
                return {"status": "started"}
        """
        # Play filler immediately
        if filler_key:
            from mateai.infrastructure.tts.audio_cache import get_cached_audio_bytes
            from mateai.interfaces.desktop.voice_controller import voice_controller
            audio = get_cached_audio_bytes(filler_key)
            if audio:
                voice_controller._play_cached_phrase_instant_async(filler_key)
            elif filler_text:
                voice_controller._play_cached_phrase_instant_async(filler_text)
        elif filler_text:
            from mateai.interfaces.desktop.voice_controller import voice_controller
            voice_controller._play_cached_phrase_instant_async(filler_text)

        # Schedule background work
        background_tasks.add_task(func, *args, **kwargs)


# ================================================================
# Module-level singleton
# ---------------------------------------------------------------------------
background_worker_manager = BackgroundWorkerManager()


# ================================================================
# Lifespan Integration (for FastAPI)
# ================================================================

@asynccontextmanager
async def background_worker_lifespan(app):
    """FastAPI lifespan context manager."""
    await background_worker_manager.start()
    yield
    await background_worker_manager.stop()


# ================================================================
# Convenience: Decorator for background tasks
# ================================================================

def background_task(
    name: Optional[str] = None,
    description: str = "",
    tts_filler: Optional[str] = None,
    tts_filler_key: Optional[str] = None,
):
    """
    Decorator để biến async function thành background task tự động.

    Usage:
        @background_task(name="monthly_report", tts_filler_key="filler_report")
        async def generate_monthly_report(month: int, year: int, progress_callback=None):
            ...
            return result

        # Gọi:
        task_id = await background_worker_manager.submit(
            "monthly_report", generate_monthly_report, 1, 2024
        )
    """
    def decorator(func: Callable) -> Callable:
        task_name = name or func.__name__

        async def wrapper(*args, **kwargs):
            return await background_worker_manager.submit(
                task_name, func, *args, description=description, tts_filler=tts_filler,
                tts_filler_key=tts_filler_key, **kwargs
            )

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        wrapper._background_task_name = task_name
        wrapper._original_func = func
        return wrapper

    return decorator