# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/fake_enterprise.py
========================
Máy chủ giả mô phỏng GIAO THỨC của các hệ thống hạ tầng doanh nghiệp, chạy thật trên 127.0.0.1 (HTTP hoặc HTTPS
tự ký) để kiểm chứng bộ nối khai báo mà không cần hạ tầng thật:

  xác thực   bearer / basic / header khoá / OAuth2 client-credentials (token hết hạn được) /
             đăng nhập lấy token kiểu vCenter (basic -> chuỗi token), GLPI (header user_token -> session_token),
             Veeam (form username/password -> access_token)
  phân trang page+per_page · offset+limit · cursor · next_url tuyệt đối · Link header · next sang máy chủ khác (bị chặn)
  JSON-RPC   kiểu Zabbix (POST /api_jsonrpc.php, Bearer)
  Prometheus kiểu /api/v1/query
  thao tác   POST /api/vms/{id}/restart · DELETE /api/vms/{id}
  kiểu nội dung lạ  application/vnd.api+json · text/plain nhưng thân JSON · HTML
  lỗi        /flaky trả 500 hai lần đầu rồi mới tốt (kiểm tra thử lại cho đọc, KHÔNG thử lại cho ghi)

Đây là mô phỏng theo tài liệu giao thức, KHÔNG thay thế việc thử trên hệ thống thật.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

ROWS = [{"id": i, "name": f"may-{i:02d}", "status": "up" if i % 5 else "down"} for i in range(1, 26)]   # 25 dòng


class State:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.oauth_tokens: set = set()
        self.sessions: set = set()
        self.n = 0
        self.flaky_hits = 0
        self.token_ttl = 3600
        self.logins = 0
        self.other_host_calls = 0
        # giám sát: tests đổi các danh sách này để mô phỏng Prometheus / Grafana
        self.prom_alerts: List[Dict[str, Any]] = []
        self.prom_targets: List[Dict[str, Any]] = []
        self.prom_vectors: Dict[str, List[Dict[str, Any]]] = {}
        self.graf_alerts: List[Dict[str, Any]] = []
        self.graf_rules: List[Dict[str, Any]] = []
        self.graf_dashboards: List[Dict[str, Any]] = []

    def next_id(self) -> int:
        self.n += 1
        return self.n


def build_app(state: State, other_origin: str = "") -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def record(request: Request, call_next):
        body = await request.body()
        state.calls.append({"method": request.method, "path": request.url.path, "query": dict(request.query_params),
                            "auth": request.headers.get("authorization", ""), "body": body.decode("utf-8", "replace"),
                            "ctype": request.headers.get("content-type", ""), "headers": dict(request.headers)})
        return await call_next(request)

    # ── xác thực ─────────────────────────────────────────────────────────
    def bearer(auth: str, good: str = "GOOD") -> bool:
        return auth == f"Bearer {good}"

    @app.get("/open/items")
    async def open_items():
        return {"items": ROWS[:5], "total": len(ROWS)}

    @app.get("/bearer/items")
    async def bearer_items(authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return {"data": ROWS[:3]}

    @app.get("/basic/items")
    async def basic_items(authorization: str = Header("")):
        want = "Basic " + base64.b64encode(b"admin:pw").decode()
        if authorization != want:
            raise HTTPException(401, "bad basic")
        return {"rows": ROWS[:2]}

    @app.get("/hdr/items")
    async def hdr_items(x_api_key: str = Header("")):
        if x_api_key != "K1":
            raise HTTPException(401, "bad key")
        return {"records": ROWS[:2]}

    @app.get("/query/items")
    async def query_items(api_key: str = ""):
        if api_key != "Q1":
            raise HTTPException(401, "bad key")
        return {"records": ROWS[:2]}

    # OAuth2 client-credentials
    @app.post("/oauth/token")
    async def oauth_token(request: Request):
        form = dict((await request.form()).items())
        basic = request.headers.get("authorization", "")
        if basic.startswith("Basic "):
            cid, _, sec = base64.b64decode(basic[6:]).decode().partition(":")
        else:
            cid, sec = form.get("client_id", ""), form.get("client_secret", "")
        if form.get("grant_type") != "client_credentials" or (cid, sec) != ("cid", "csecret"):
            raise HTTPException(401, "invalid_client")
        tok = f"AT-{state.next_id()}"
        state.oauth_tokens.add(tok)
        return {"access_token": tok, "expires_in": state.token_ttl, "token_type": "Bearer", "scope": form.get("scope", "")}

    @app.get("/oauth/items")
    async def oauth_items(authorization: str = Header("")):
        if not authorization.startswith("Bearer ") or authorization[7:] not in state.oauth_tokens:
            raise HTTPException(401, "expired")
        return {"items": ROWS[:4]}

    @app.post("/test/expire")
    async def expire():
        state.oauth_tokens.clear()
        state.sessions.clear()
        return {"ok": True}

    # đăng nhập kiểu vCenter: basic -> chuỗi token (JSON string)
    @app.post("/vc/api/session")
    async def vc_session(authorization: str = Header("")):
        if authorization != "Basic " + base64.b64encode(b"administrator@vsphere.local:pw").decode():
            raise HTTPException(401, "bad creds")
        state.logins += 1
        tok = f"VC-{state.next_id()}"
        state.sessions.add(tok)
        return JSONResponse(tok)

    @app.get("/vc/api/vcenter/vm")
    async def vc_vms(request: Request):
        if request.headers.get("vmware-api-session-id") not in state.sessions:
            raise HTTPException(401, "no session")
        return [{"vm": f"vm-{i}", "name": f"srv-{i}", "power_state": "POWERED_ON"} for i in range(1, 4)]

    # đăng nhập kiểu GLPI: GET + Authorization: user_token X -> session_token
    @app.get("/glpi/apirest.php/initSession")
    async def glpi_init(authorization: str = Header("")):
        if authorization != "user_token UT123":
            raise HTTPException(401, "bad user token")
        state.logins += 1
        tok = f"G-{state.next_id()}"
        state.sessions.add(tok)
        return {"session_token": tok}

    @app.get("/glpi/apirest.php/Ticket")
    async def glpi_tickets(request: Request):
        if request.headers.get("session-token") not in state.sessions:
            raise HTTPException(401, "no session")
        return [{"id": i, "name": f"Ticket {i}"} for i in range(1, 4)]

    # đăng nhập kiểu Veeam: form password grant
    @app.post("/veeam/api/oauth2/token")
    async def veeam_token(request: Request):
        form = dict((await request.form()).items())
        if form.get("grant_type") != "password" or (form.get("username"), form.get("password")) != ("veeam", "pw"):
            raise HTTPException(400, "invalid_grant")
        state.logins += 1
        tok = f"V-{state.next_id()}"
        state.sessions.add(tok)
        return {"access_token": tok, "token_type": "bearer", "expires_in": 900}

    @app.get("/veeam/api/v1/jobs")
    async def veeam_jobs(authorization: str = Header("")):
        if not authorization.startswith("Bearer ") or authorization[7:] not in state.sessions:
            raise HTTPException(401, "no token")
        return {"data": [{"name": "Backup-A", "status": "Success"}, {"name": "Backup-B", "status": "Warning"}]}

    # ── phân trang ───────────────────────────────────────────────────────
    @app.get("/pg/items")
    async def pg_items(page: int = 1, per_page: int = 10):
        s = (page - 1) * per_page
        return {"data": ROWS[s:s + per_page], "total": len(ROWS)}

    @app.get("/off/items")
    async def off_items(offset: int = 0, limit: int = 10):
        return {"results": ROWS[offset:offset + limit], "count": len(ROWS)}

    @app.get("/cur/items")
    async def cur_items(cursor: str = ""):
        start = int(cursor.removeprefix("c")) if cursor else 0
        nxt = start + 10
        return {"items": ROWS[start:nxt], "meta": {"next_cursor": f"c{nxt}" if nxt < len(ROWS) else None}}

    @app.get("/nu/items")
    async def nu_items(request: Request, page: int = 1):
        s = (page - 1) * 10
        nxt = f"{request.base_url}nu/items?page={page + 1}" if s + 10 < len(ROWS) else None
        return {"items": ROWS[s:s + 10], "next": nxt}

    @app.get("/lh/items")
    async def lh_items(request: Request, page: int = 1):
        s = (page - 1) * 10
        resp = JSONResponse(ROWS[s:s + 10])
        if s + 10 < len(ROWS):
            resp.headers["Link"] = f'<{request.base_url}lh/items?page={page + 1}>; rel="next"'
        return resp

    @app.get("/evil/items")
    async def evil_items():
        return {"items": ROWS[:2], "next": f"{other_origin}/steal?x=1"}

    @app.get("/same/items")
    async def same_items(page: int = 1):                    # bỏ qua tham số trang -> luôn trả cùng một trang
        return {"data": ROWS[:10], "total": 99}

    # ── JSON-RPC (Zabbix) và Prometheus ──────────────────────────────────
    @app.post("/zabbix/api_jsonrpc.php")
    async def zabbix(request: Request, authorization: str = Header("")):
        if not bearer(authorization, "ZBXTOKEN"):
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params.", "data": "Not authorised."}, "id": 1}
        req = await request.json()
        if req.get("method") == "problem.get":
            return {"jsonrpc": "2.0", "result": [{"eventid": "1", "name": "High CPU on srv-1", "severity": "4"},
                                                 {"eventid": "2", "name": "Disk full on srv-2", "severity": "5"}], "id": req["id"]}
        if req.get("method") == "host.get":
            return {"jsonrpc": "2.0", "result": [{"hostid": "10", "host": "srv-1"}], "id": req["id"]}
        return {"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found."}, "id": req.get("id")}

    @app.get("/prom/api/v1/alerts")
    async def prom_alerts():
        return {"status": "success", "data": {"alerts": state.prom_alerts}}

    @app.get("/prom/api/v1/targets")
    async def prom_targets():
        return {"status": "success", "data": {"activeTargets": state.prom_targets, "droppedTargets": []}}

    @app.get("/graf/api/alertmanager/grafana/api/v2/alerts")
    async def graf_alerts(authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return state.graf_alerts

    @app.get("/graf/api/prometheus/grafana/api/v1/rules")
    async def graf_rules(authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return {"status": "success", "data": {"groups": [{"name": "g", "rules": state.graf_rules}]}}

    @app.get("/graf/api/search")
    async def graf_search(authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return state.graf_dashboards

    @app.get("/prom/api/v1/query")
    async def prom_query(query: str = ""):
        for key, vec in state.prom_vectors.items():
            if key in query:
                return {"status": "success", "data": {"resultType": "vector", "result": vec}}
        if state.prom_vectors:
            return {"status": "success", "data": {"resultType": "vector", "result": []}}
        return {"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": {"__name__": "up", "instance": "srv-1:9100"}, "value": [1700000000, "0" if "== 0" in query else "1"]}]}}

    # ── Paperless-ngx (connector cũ) ─────────────────────────────────────
    @app.get("/api/")
    async def paperless_root(authorization: str = Header("")):
        if authorization != "Token PL-TOKEN":
            raise HTTPException(401, "Invalid token.")
        return {"version": "2.11.0"}

    @app.get("/api/documents/")
    async def paperless_docs(request: Request, authorization: str = Header("")):
        if authorization != "Token PL-TOKEN":
            raise HTTPException(401, "Invalid token.")
        q = request.query_params.get("query", "")
        docs = [{"id": i, "title": f"Hợp đồng {i}", "correspondent": 3, "document_type": 1, "tags": [], "created": "2026-09-01",
                 "added": "2026-09-02T08:00:00Z", "content": f"nội dung {q} {i}"} for i in range(1, 4)]
        return {"count": 3, "results": docs}

    # ── thao tác can thiệp ───────────────────────────────────────────────
    @app.post("/api/vms/{vm_id}/restart")
    async def restart(vm_id: str, authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return {"task": f"T-{vm_id}", "state": "queued"}

    @app.delete("/api/vms/{vm_id}")
    async def delete_vm(vm_id: str, authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return {"deleted": vm_id}

    @app.post("/api/tickets")
    async def create_ticket(request: Request, authorization: str = Header("")):
        if not bearer(authorization):
            raise HTTPException(401, "bad token")
        return JSONResponse({"id": 777, "echo": await request.json()}, status_code=201)

    # ── nội dung lạ / lỗi ────────────────────────────────────────────────
    @app.get("/odd/vnd")
    async def odd_vnd():
        return Response(json.dumps({"data": ROWS[:2]}), media_type="application/vnd.api+json")

    @app.get("/odd/plain")
    async def odd_plain():
        return PlainTextResponse(json.dumps({"items": ROWS[:2]}))

    @app.get("/odd/html")
    async def odd_html():
        return Response("<html><body>Login</body></html>", media_type="text/html")

    @app.get("/flaky")
    async def flaky():
        state.flaky_hits += 1
        if state.flaky_hits <= 2:
            raise HTTPException(500, "boom")
        return {"items": ROWS[:2]}

    @app.post("/flaky-write")
    async def flaky_write():
        state.flaky_hits += 1
        raise HTTPException(500, "boom")

    @app.get("/leak")
    async def leak(request: Request):                         # máy chủ "xấu" phản chiếu header xác thực trong thông báo lỗi
        return PlainTextResponse(f"denied for {request.headers.get('authorization', '')} {request.headers.get('x-api-key', '')}",
                                 status_code=400)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/healthz-text")
    async def health_text():
        return PlainTextResponse("OK")

    return app


class FakeServer:
    def __init__(self, tls: bool = False, other_origin: str = "") -> None:
        self.state = State()
        self.tls = tls
        self._dir: Optional[tempfile.TemporaryDirectory] = None
        self.ca_file: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._server: Any = None
        self.other_origin = other_origin
        self.port = 0

    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}"

    def _make_cert(self) -> Dict[str, str]:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-enterprise")])
        now = _dt.datetime.now(_dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - _dt.timedelta(minutes=5))
                .not_valid_after(now + _dt.timedelta(days=2))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        self._dir = tempfile.TemporaryDirectory()
        d = Path(self._dir.name)
        (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (d / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                      serialization.NoEncryption()))
        self.ca_file = str(d / "cert.pem")                      # tự ký: chính nó là CA
        return {"ssl_certfile": str(d / "cert.pem"), "ssl_keyfile": str(d / "key.pem")}

    def start(self) -> "FakeServer":
        import uvicorn
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        kwargs = self._make_cert() if self.tls else {}
        cfg = uvicorn.Config(build_app(self.state, self.other_origin), host="127.0.0.1", port=self.port,
                             log_level="critical", **kwargs)
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("Máy chủ giả không khởi động được")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
        if self._dir:
            self._dir.cleanup()

    def __enter__(self) -> "FakeServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def calls_to(self, path_prefix: str) -> List[Dict[str, Any]]:
        return [c for c in self.state.calls if c["path"].startswith(path_prefix)]
