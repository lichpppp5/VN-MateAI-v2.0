# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/fake_master.py
====================
Ubuntu Master GIẢ theo HỢP ĐỒNG Master Control API v1 (docs/integrations/dev-fleet.md) — để kiểm thử bộ nối và dịch vụ
Dev Fleet mà không cần cụm thật. Đây là mô phỏng theo hợp đồng do VN-MateAI định nghĩa; KHÔNG phải bản ghi lại hành vi của
Master / OpenClaw thật (chưa có quyền truy cập hệ thống thật khi viết).

Điều khiển từ test: `master.workers`, `master.set_task(...)`, `master.fail_next(n)`, `master.git_head`, `master.token`.
"""
from __future__ import annotations

import datetime as _dt
import socket
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class FakeMaster:
    def __init__(self, token: str = "TOKEN-MASTER", api_version: str = "1.0") -> None:
        self.token = token
        self.api_version = api_version
        self.workers: List[Dict[str, Any]] = [
            {"worker_id": "mac-01", "hostname": "worknode-01", "platform": "macos", "architecture": "arm64", "os_version": "15.1",
             "state": "idle", "cpu": 12, "memory": 40, "disk": 55, "capabilities": ["git", "python", "nodejs", "docker"],
             "openclaw_status": "online", "openclaw_version": "x.y", "last_seen": _now()},
            {"worker_id": "mac-02", "hostname": "worknode-02", "platform": "macos", "architecture": "arm64", "os_version": "15.1",
             "state": "idle", "cpu": 70, "memory": 60, "disk": 50, "capabilities": ["git", "python", "xcode", "ios"],
             "openclaw_status": "online", "last_seen": _now()},
            {"worker_id": "mac-03", "hostname": "worknode-03", "platform": "macos", "state": "offline",
             "capabilities": ["git", "python"], "last_seen": "2020-01-01T00:00:00Z"},
        ]
        self.agents: List[Dict[str, Any]] = [{"agent_id": "agent-be-01", "worker_id": "mac-01", "role": "Backend Developer",
                                              "status": "idle", "runtime": "openclaw", "model": "m", "provider": "9router"}]
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.idempotent: Dict[str, Dict[str, Any]] = {}
        self.dispatch_count = 0
        self.git_head = ""
        self.calls: List[Dict[str, Any]] = []
        self._fail = 0
        self.port = 0
        self._server: Any = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def fail_next(self, n: int = 1) -> None:
        self._fail = n

    def set_task(self, task_id: str, **fields: Any) -> None:
        self.tasks.setdefault(task_id, {"task_id": task_id}).update(fields)

    # ── ứng dụng ────────────────────────────────────────────────────────────
    def _app(self) -> FastAPI:
        app = FastAPI()
        me = self

        def guard(request: Request, authorization: str) -> None:
            me.calls.append({"method": request.method, "path": request.url.path, "idem": request.headers.get("idempotency-key")})
            if me._fail > 0:
                me._fail -= 1
                raise HTTPException(503, "temporarily unavailable")
            if authorization != f"Bearer {me.token}":
                raise HTTPException(401, "bad token")

        @app.get("/api/v1/fleet/health")
        async def health(request: Request, authorization: str = Header("")):
            guard(request, authorization)
            return {"api_version": me.api_version, "server_time": _now(),
                    "master": {"master_id": "master-1", "name": "Ubuntu-Master", "hostname": "ubuntu-dev", "ip": "10.0.0.5",
                               "os": "Ubuntu 24.04", "version": "0.9", "ansible_status": "online", "router_status": "online",
                               "openclaw_status": "online", "fleet_status": "healthy", "health": "healthy"}}

        @app.get("/api/v1/fleet/workers")
        async def workers(request: Request, authorization: str = Header("")):
            guard(request, authorization)
            return {"workers": me.workers}

        @app.get("/api/v1/fleet/workers/{wid}")
        async def worker(wid: str, request: Request, authorization: str = Header("")):
            guard(request, authorization)
            for w in me.workers:
                if w["worker_id"] == wid:
                    return w
            raise HTTPException(404, "no such worker")

        @app.get("/api/v1/fleet/workers/{wid}/metrics")
        async def metrics(wid: str, request: Request, authorization: str = Header("")):
            guard(request, authorization)
            return {"worker_id": wid, "cpu": 12, "memory": 40, "disk": 55}

        @app.get("/api/v1/fleet/workers/{wid}/git")
        async def git(wid: str, request: Request, repository: str = "", authorization: str = Header("")):
            guard(request, authorization)
            if not me.git_head:
                raise HTTPException(404, "no repo")
            return {"repository": repository, "head": me.git_head, "branch": "main", "dirty": False}

        @app.get("/api/v1/fleet/agents")
        async def agents(request: Request, authorization: str = Header("")):
            guard(request, authorization)
            return {"agents": me.agents}

        @app.post("/api/v1/fleet/tasks/dispatch")
        async def dispatch(request: Request, authorization: str = Header(""), idempotency_key: str = Header("")):
            guard(request, authorization)
            if not idempotency_key:
                raise HTTPException(400, "Idempotency-Key required")
            if idempotency_key in me.idempotent:
                return me.idempotent[idempotency_key]
            body = await request.json()
            run_id = body["task"]["run_id"]
            me.dispatch_count += 1
            me.tasks[run_id] = {"task_id": run_id, "status": "QUEUED", "worker_id": body["worker_id"], "agent_id": "agent-be-01",
                                "spec": body["task"]}
            reply = {"task_id": run_id, "status": "QUEUED", "worker_id": body["worker_id"], "agent_id": "agent-be-01"}
            me.idempotent[idempotency_key] = reply
            return reply

        @app.get("/api/v1/fleet/tasks/{tid}")
        async def task(tid: str, request: Request, authorization: str = Header("")):
            guard(request, authorization)
            if tid not in me.tasks:
                raise HTTPException(404, "no such task")
            return {k: v for k, v in me.tasks[tid].items() if k != "spec"}

        @app.get("/api/v1/fleet/tasks/{tid}/logs")
        async def logs(tid: str, request: Request, tail: int = 200, authorization: str = Header("")):
            guard(request, authorization)
            if tid not in me.tasks:
                raise HTTPException(404, "no such task")
            return {"task_id": tid, "lines": ["started", "running tests"]}

        @app.post("/api/v1/fleet/tasks/{tid}/{verb}")
        async def control(tid: str, verb: str, request: Request, authorization: str = Header("")):
            guard(request, authorization)
            if tid not in me.tasks:
                raise HTTPException(404, "no such task")
            if verb == "cancel":
                me.tasks[tid]["status"] = "CANCELLED"
            return {"task_id": tid, "status": me.tasks[tid]["status"]}

        return app

    def start(self) -> "FakeMaster":
        import uvicorn
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self._server = uvicorn.Server(uvicorn.Config(self._app(), host="127.0.0.1", port=self.port, log_level="critical"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("Master giả không khởi động được")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "FakeMaster":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
