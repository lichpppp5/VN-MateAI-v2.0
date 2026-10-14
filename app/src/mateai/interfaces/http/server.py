# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/server.py
================================
Dựng ứng dụng FastAPI của VN-MateAI: CORS, middleware xác thực, gắn route, listener
cổng IoT, móc khởi động / tắt máy.

Supervisor Phase 10 (§198) — file này KHÔNG chứa nghiệp vụ:
  - route: `interfaces/http/routes.py` (thứ tự cố định bằng test hợp đồng);
  - khởi động / tắt dịch vụ nền: `interfaces/http/lifecycle.py`;
  - health probe: `interfaces/http/routers/probes.py`.
`main.py` import `app` và `iot_listener_app` từ đây.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http import lifecycle, ws_auth
from mateai.interfaces.http.routes import register_routes

# Silence Windows WinError 10054 in asyncio Proactor _call_connection_lost
if sys.platform == "win32":
    try:
        import asyncio.proactor_events
        _orig_call_conn_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _safe_call_conn_lost(self, exc):
            try:
                _orig_call_conn_lost(self, exc)
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _safe_call_conn_lost
    except Exception:
        pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="VN-MateAI",
    description=(
        "Autonomous Voice-RPA Windows Assistant API — Phase 2, 4 & 10. "
        "Supports REST voice commands, streaming TTS, real-time "
        "ESP32 Multi-Node WebSocket audio pipeline, Auth & RBAC Web Portal."
    ),
    version="2.1.0",
    contact={"name": "Dương Thanh Lịch", "url": "https://github.com/lichpppp/VNMateAI-v1.0", "email": "thanhlich.duong@gmail.com"},
    license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    docs_url="/docs",
    redoc_url="/redoc",
)

# Zero-Trust CORS
#
# Trước đây: allow_origins=["*"] + allow_credentials=True — tổ hợp nguy hiểm cho
# phép mọi website trên internet gọi API có kèm credential của người dùng.
#
# Nay: web portal được phục vụ từ CHÍNH server này nên request là same-origin và
# không cần CORS header. Danh sách origin cho phép lấy từ biến môi trường
# (VNMATEAI_CORS_ORIGINS, phân tách bằng dấu phẩy) cho các triển khai tách frontend.
# allow_credentials=False: xác thực dùng Bearer token trong header, không dùng
# cookie, nên không có gì để trình duyệt tự động gửi đi cả.
_allowed_origins = [
    o.strip()
    for o in os.getenv("VNMATEAI_CORS_ORIGINS", "").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ─── Request ID + mô hình lỗi chuẩn (prompt cuối §95, §143) ──────────────────
#: Id của request đang xử lý — một nguồn ở `config/log_setup` (log của request mang theo).
from mateai.config.log_setup import REQUEST_ID  # noqa: E402
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Lỗi không bắt được: `{"error": {code, message, request_id}}`, không lộ stack trace
    hay thông điệp ngoại lệ (có thể chứa đường dẫn / dữ liệu nhạy cảm). Chi tiết chỉ
    nằm trong log máy chủ, tra theo request_id. Lỗi nghiệp vụ (HTTPException) giữ
    dạng `detail` mà giao diện đang đọc."""
    rid = request.headers.get("x-request-id", "")
    rid = rid if _SAFE_REQUEST_ID.match(rid) else REQUEST_ID.get()
    if rid == "-":
        rid = uuid.uuid4().hex[:16]
    logger.error("Lỗi không bắt được [request_id=%s] %s %s", rid, request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, headers={"X-Request-ID": rid}, content={"error": {
        "code": "internal_error",
        "message": f"Lỗi máy chủ nội bộ. Báo quản trị kèm mã {rid} để tra log.",
        "request_id": rid,
    }})


# ─── Authentication Middleware (Zero-Trust RBAC Protection) ─────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Bảo vệ tất cả các REST API endpoint.

    Zero-Trust: KHÔNG có fallback. Mọi endpoint ngoài danh sách public đều bắt buộc
    phải mang JWT hợp lệ.

    (Trước đây từng tự cấp toàn quyền khi request đến từ loopback HOẶC khi header
    Referer chứa "/hud". Cả hai đều do phía client kiểm soát được, nên bất kỳ máy
    nào trong LAN cũng giành được quyền admin. Đã gỡ bỏ.)
    """
    path = request.url.path
    # Chỉ những gì PHẢI chạy trước khi đăng nhập. Mọi endpoint ghi/điều khiển
    # (computer-use/dispatch, screenshot, topology save/reset, departments/save,
    # cross-report, ephemeral-cache/flush...) từng nằm ở đây — tức ai trong LAN
    # cũng gọi được không cần JWT. Đã chuyển hết sang yêu cầu đăng nhập.
    public_endpoints = (
        "/api/v1/login",
        "/api/v1/login/",
        "/api/v1/login/mfa",               # bước 2 của đăng nhập MFA (tự kiểm token trung gian + mã)
        # SSO (OIDC): chạy trước khi đăng nhập; tự kiểm state / nonce / PKCE / chữ ký id_token (application/security/sso.py)
        "/api/v1/sso/config", "/api/v1/sso/login", "/api/v1/sso/callback", "/api/v1/sso/exchange",
        "/api/v1/config/assistant-name",   # màn hình HUD/đăng nhập
        "/api/v1/health-dashboard",        # telemetry HUD chế độ xem
        # Agent máy trạm: route tự kiểm mã đăng ký dùng một lần / khoá thiết bị
        # (routers/agent_devices.py) — Agent không có tài khoản người dùng.
        "/api/v1/agent/enroll",
        "/api/v1/agent/update/manifest",
        "/api/v1/agent/update/package",
    )
    # Endpoint của máy worker (không có tài khoản người dùng): chỉ nhận
    # enrollment secret của worker hoặc JWT admin/manager — cùng luật với
    # /ws/client. JWT của người dùng thường KHÔNG đủ để giả làm worker.
    worker_endpoints = ("/api/v1/worknodes/heartbeat",)

    # Mọi tiền tố path phải được bọc xác thực. /api/erp/ là router của
    # core/api_erp.py — trước đây nằm ngoài /api/v1/ nên KHÔNG endpoint nào của
    # nó được middleware kiểm tra (chỉ có 4/5 endpoint tự khai Depends riêng,
    # còn /template thì hoàn toàn không xác thực).
    guarded_prefixes = ("/api/v1/", "/api/erp/")

    # Phase 78: bỏ qua preflight CORS (OPTIONS).
    #
    # Trình duyệt gửi OPTIONS KHÔNG kèm Authorization, nên middleware này trả
    # 401 và CORSMiddleware không bao giờ kịp gắn header. Kết quả: mọi
    # frontend chạy khác origin (trang Admin ở cổng 3001) không gọi được API
    # nào — lỗi "Failed to fetch" dù backend vẫn chạy bình thường.
    #
    # Đây KHÔNG phải nới lỏng xác thực: preflight chỉ hỏi "có cho phép
    # method/header này không", không mang dữ liệu, không chạy nghiệp vụ, và
    # không đọc được gì. Request thật (GET/POST/...) vẫn qua đủ kiểm tra JWT
    # bên dưới.
    if request.method == "OPTIONS":
        return await call_next(request)

    if path.startswith(guarded_prefixes):
        if path in public_endpoints:
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "token" in request.query_params:
            token = request.query_params.get("token")

        # Webhook kích hoạt kịch bản: route tự kiểm token webhook RIÊNG của kịch bản (không phải JWT).
        if request.method == "POST" and re.fullmatch(r"/api/v1/playbooks/[a-z0-9][a-z0-9_-]{1,48}/trigger", path):
            return await call_next(request)

        # Agent ngoài (Danh bạ Agent): CHỈ nhận token agent riêng — JWT người dùng không thay được. Route kiểm lại lần nữa.
        if path.startswith("/api/v1/agents/inbound/"):
            from mateai.application.agent_gateway import directory as _agent_dir
            _row, _why = _agent_dir.authenticate(token or "")
            if _row:
                return await call_next(request)
            return JSONResponse(status_code=401 if "không hợp lệ" in _why else 403, content={"detail": _why},
                                headers={"WWW-Authenticate": "Bearer"})

        if path in worker_endpoints:
            if token and ws_auth.is_valid_worker_token(token):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "Worker chưa xác thực (thiếu/sai enrollment token)."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Yêu cầu xác thực tài khoản (Thiếu Bearer Token)."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Token trung gian (scope mfa / mfa_enroll) KHÔNG phải token truy cập; riêng 3 đường cài MFA nhận `mfa_enroll`.
        mfa_enroll_paths = ("/api/v1/auth/mfa/status", "/api/v1/auth/mfa/setup", "/api/v1/auth/mfa/enable")
        payload = auth_manager.decode_access_token(token, allow_scopes=("mfa_enroll",) if path in mfa_enroll_paths else ())
        if not payload or "sub" not in payload:
            return JSONResponse(
                status_code=401,
                content={"detail": "Phiên đăng nhập đã hết hạn hoặc không hợp lệ."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


# Khai báo SAU middleware xác thực => bọc NGOÀI cùng: cả phản hồi 401 cũng có X-Request-ID.
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Nhận `X-Request-ID` hợp lệ từ client (để nối log hai phía), không thì tự sinh;
    luôn trả lại trong header. Giá trị lạ không được phản chiếu."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
    token = REQUEST_ID.set(rid)
    from mateai.infrastructure.observability.tracing import set_attrs, span
    try:
        with span("http.request", **{"http.method": request.method, "http.target": request.url.path,
                                     "http.request_id": rid}) as sp:
            response = await call_next(request)
            set_attrs(sp, **{"http.status_code": response.status_code})
    finally:
        REQUEST_ID.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


register_routes(app)


# ── Listener IoT không TLS (settings.IOT_PORT, mặc định 8000) ─────────────
# ESP32/Xiaozhi nói WS thường (TLS làm tràn heap của chip). Trước đây cổng này
# phục vụ NGUYÊN app: đăng nhập, mọi API, portal — mật khẩu và JWT đi qua LAN
# dạng rõ. Nay chỉ cho qua đường của thiết bị + health probe; còn lại dùng HTTPS.
_IOT_PORT_PREFIXES = ("/api/v1/xiaozhi/ws", "/ws/audio-stream")
_IOT_PORT_EXACT = {"/livez", "/readyz", "/startupz"}


def _iot_port_allows(path: str) -> bool:
    return path in _IOT_PORT_EXACT or any(path == p or path.startswith(p + "/") for p in _IOT_PORT_PREFIXES)


async def iot_listener_app(scope: Dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI app cho listener cổng IoT: lọc đường dẫn rồi chuyển cho `app`."""
    if scope["type"] in ("http", "websocket") and not _iot_port_allows(scope.get("path", "")):
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body",
                        "body": "Không phục vụ trên cổng IoT không mã hoá — dùng HTTPS.".encode("utf-8")})
        else:
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
        return
    await app(scope, receive, send)


@app.on_event("startup")
async def _on_startup() -> None:
    """Chạy các bước khởi động (`lifecycle.default_steps()`), một lần cho cả hai listener."""
    await lifecycle.run_startup(app)


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """Dừng dịch vụ nền, đóng pool HTTP."""
    await lifecycle.run_shutdown()
