# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/workers.py
=========================================
Worker node (client agent): bật/tắt worker cục bộ trên máy chủ, đóng gói
"Tải Agent" cho máy trạm, và địa chỉ máy chủ mà worker phải nối tới.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import socket as _socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse

from mateai.config.loader import settings
from mateai.interfaces.http import enrollment
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()

_PROJECT_ROOT = Path(settings.PROJECT_ROOT)
# Gói "Tải Agent" đóng từ chính client_agent/ (Phase 6: gỡ bản fork client_template/,
# hai bản đã lệch nhau — mỗi bên sửa một lỗi mà bên kia vẫn còn).
_CLIENT_AGENT_DIR = _PROJECT_ROOT / "client_agent"


_local_worker_process: Optional[subprocess.Popen] = None


_LOCAL_WORKER_READY_TIMEOUT = 8.0


def _resolve_master_endpoint(request: Request) -> tuple[str, str, int]:
    """Trả về `(scheme, host, port)` mà một Client Agent phải nối tới.

    Đọc từ chính yêu cầu đang đến (`request.url`), không đọc `config.json`.
    Lý do: `config.json` để lại `PORT: 443` từ lâu trong khi máy chủ thật
    được khởi chạy bằng `uvicorn ... --port 8000` — cấu hình lệch với thực tế
    là nguồn của URL không bắt tay được. Yêu cầu thì không thể lệch: nó phản
    ánh đúng cổng và scheme mà trình duyệt vừa dùng để mở trang này.

    `loopback_only=True` ép về 127.0.0.1 — dùng cho worker chạy ngay trên
    máy chủ. Worker tải về chạy ở máy khác nên KHÔNG ép (xem
    `_local_worker_ws_url` và `download_agent`).
    """
    url = request.url
    scheme = "wss" if url.scheme in ("https", "wss") else "ws"
    host = url.hostname or "127.0.0.1"
    # `0.0.0.0` / `::` là địa chỉ "mọi giao diện", không phải địa chỉ nào để
    # kết nối tới. Ở đây coi như loopback; nếu sau reverse proxy bị rơi vào
    # giá trị này thì thà chỉ loopback còn hơn sinh ra URL không dùng được.
    if host in ("0.0.0.0", "::", "[::]", ""):
        host = "127.0.0.1"
    port = url.port or (443 if scheme == "wss" else 80)
    return scheme, host, port


def _master_ws_url(request: Request) -> str:
    """URL WebSocket cho Client Agent ở MÁY KHÁC (gói tải về).

    Khác `_local_worker_ws_url` ở chỗ không ép loopback: máy con ở trong LAN
    phải nối tới địa chỉ mà máy chủ thực sự nghe, nên dùng IP mà người dùng
    đang truy cập.
    """
    scheme, host, port = _resolve_master_endpoint(request)
    return f"{scheme}://{host}:{port}/ws/client"


def _local_worker_ws_url(request: Request) -> str:
    """Dựng URL WebSocket mà worker cục bộ phải nối tới.

    Trước đây URL này ghi cứng `"wss://127.0.0.1:443/ws/client"`, sai trên
    hai điểm một lúc:

      * Cổng. `config.json` để lại `PORT: 443` từ lâu, còn máy chủ thật được
        khởi chạy bằng `uvicorn ... --port 8000`. Không có gì lắng nghe 443.
      * Scheme. Máy chủ chạy HTTP thuần, nên `wss://` (WebSocket over TLS)
        không bao giờ bắt tay được với nó.

    Cả hai lỗi đều im lặng: tiến trình vẫn sinh ra, vẫn nhận PID, chỉ là
    không bao giờ kết nối được.

    Nay dựng URL từ chính yêu cầu đang đến: admin bấm nút trên cổng nào thì
    worker nối về đúng cổng đó, đúng scheme đó — không đoán, không phụ thuộc
    cấu hình lệch với thực tế. Worker này chạy cùng máy chủ nên quay về
    loopback.
    """
    scheme, host, port = _resolve_master_endpoint(request)
    return f"{scheme}://127.0.0.1:{port}/ws/client"


@router.get(
    "/api/v1/orchestrator/local-worker/status",
    summary="Kiểm tra trạng thái Worker Node cục bộ",
    tags=["Orchestrator"],
)
async def get_local_worker_status(
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kiểm tra xem Worker Node cục bộ có đang chạy hay không."""
    global _local_worker_process
    is_running = _local_worker_process is not None and _local_worker_process.poll() is None
    return {
        "active": is_running,
        "pid": _local_worker_process.pid if is_running else None,
        "client_id": "MASTER_LOCAL_WORKER",
    }


@router.post(
    "/api/v1/orchestrator/local-worker/toggle",
    summary="Bật hoặc tắt Worker Node cục bộ",
    tags=["Orchestrator"],
)
async def toggle_local_worker_endpoint(
    request: Request,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Khởi chạy hoặc dừng Worker Node cục bộ trên máy chủ Master.

    Chỉ báo "thành công" khi worker THỰC SỰ đăng ký, xem `_local_worker_ws_url`
    và phần kiểm chứng bên dưới.
    """
    global _local_worker_process
    # Import cục bộ: `orchestrator` là singleton sống ở mateai.interfaces.websocket.client_orchestrator, các
    # endpoint khác cũng import kiểu này chứ không nằm ở phạm vi module.
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    if _local_worker_process is not None and _local_worker_process.poll() is None:
        try:
            _local_worker_process.terminate()
            _local_worker_process.wait(timeout=3)
        except Exception:
            try:
                _local_worker_process.kill()
            except Exception:
                pass
        _local_worker_process = None
        logger.info("Local Worker Node [MASTER_LOCAL_WORKER] đã dừng.")
        return {"active": False, "message": "Đã dừng Worker Node cục bộ thành công."}
    else:
        agent_script = _PROJECT_ROOT / "client_agent" / "agent.py"
        if not agent_script.exists():
            return {
                "active": False,
                "message": f"Không tìm thấy {agent_script} — không thể khởi chạy worker.",
            }

        # Chặn khởi chạy trùng. `_local_worker_process` sống trong bộ nhớ của
        # tiến trình máy chủ, nên sau mỗi lần restart nó về None — còn worker
        # thì vẫn còn sống và tự nối lại. Bấm "Bật" lúc đó sẽ sinh ra worker
        # thứ hai dùng chung một `client_id`, hai tiến trình tranh nhau đăng ký
        # cùng một tên và danh sách client hiện ra loạn.
        _already = any(
            str((c or {}).get("client_id") or (c or {}).get("id") or "") == "MASTER_LOCAL_WORKER"
            for c in orchestrator.get_connected_clients()
        )
        if _already:
            return {
                "active": True,
                "message": (
                    "Worker Node [MASTER_LOCAL_WORKER] đã có sẵn trong danh sách client — "
                    "không khởi chạy thêm. Nếu đó là worker cũ sót lại từ lần chạy trước, "
                    "hãy tắt nó ở máy đó rồi bấm Bật lại."
                ),
            }

        ws_url = _local_worker_ws_url(request)
        # Zero-Trust: truyền enrollment secret cho worker cục bộ qua env var.
        _worker_env = os.environ.copy()
        _worker_env["VNMATE_ENROLLMENT_TOKEN"] = enrollment.get_worker_enrollment_secret()
        _local_worker_process = subprocess.Popen(
            [
                sys.executable,
                str(agent_script),
                "--server",
                ws_url,
                "--id",
                "MASTER_LOCAL_WORKER",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_worker_env,
        )
        pid = _local_worker_process.pid
        logger.info("Local Worker Node [MASTER_LOCAL_WORKER] đã khởi chạy (PID: %s) qua %s.", pid, ws_url)

        # ── Kiểm chứng, đừng báo thành công bừa ────────────────────────────
        #
        # Trước đây hàm báo "khởi chạy thành công!" ngay sau `Popen`, tức là
        # chỉ cần tiến trình được sinh ra là báo thành công — dù nó nối vào
        # một cổng không ai lắng nghe. Đo thật: bấm "Bật", API trả về
        # `active: true` kèm PID, nhưng sau 5 giây danh sách client vẫn rỗng
        # và worker không bao giờ kết nối. Người dùng tin thông báo đó rồi
        # tưởng hệ thống đã chạy, trong khi thực tế nó chết lặng lẽ.
        #
        # Nay đợi worker thật sự hiện trong registry rồi mới báo thành công.
        deadline = time.time() + _LOCAL_WORKER_READY_TIMEOUT
        registered = False
        while time.time() < deadline:
            await asyncio.sleep(0.25)
            proc = _local_worker_process
            if proc is None:
                break
            if proc.poll() is not None:
                # Tiến trình tự tắt (lỗi kết nối, thiếu thư viện, ...)
                _local_worker_process = None
                logger.warning("Local Worker thoát ngay sau khi khởi chạy (mã %s).", proc.returncode)
                return {
                    "active": False,
                    "message": (
                        f"Worker không khởi động được: tiến trình thoát ngay (mã {proc.returncode}). "
                        f"Kiểm tra tại sao bằng: python3 {agent_script} --server {ws_url} --id MASTER_LOCAL_WORKER"
                    ),
                }
            if any(
                str((c or {}).get("client_id") or (c or {}).get("id") or "") == "MASTER_LOCAL_WORKER"
                for c in orchestrator.get_connected_clients()
            ):
                registered = True
                break

        if not registered:
            # Dừng luôn: để một tiến trình nối vào hư không thì chỉ là rác.
            try:
                _local_worker_process.terminate()
                _local_worker_process.wait(timeout=3)
            except Exception:
                try:
                    _local_worker_process.kill()
                except Exception:
                    pass
            _local_worker_process = None
            logger.warning(
                "Local Worker không đăng ký được sau %.1fs qua %s.", _LOCAL_WORKER_READY_TIMEOUT, ws_url
            )
            return {
                "active": False,
                "message": (
                    f"Worker chạy được nhưng không kết nối được về máy chủ tại {ws_url} "
                    f"trong {_LOCAL_WORKER_READY_TIMEOUT:.0f} giây — đã dừng tiến trình. "
                    "Kiểm tra cổng này còn chạy không."
                ),
            }

        return {
            "active": True,
            "pid": pid,
            "message": "Worker Node cục bộ [MASTER_LOCAL_WORKER] đã kết nối thành công.",
        }


def _get_server_local_ip() -> str:
    """Detect LAN IP of the Master Server for injecting into agent config."""
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def bundled_agent_version():
    from mateai.interfaces.websocket.client_orchestrator import bundled_agent_version as _v
    return _v()


@router.get(
    "/api/v1/download-agent",
    summary="Phase 20: Tải xuống Client Agent được đóng gói động kèm config",
    tags=["Distribution"],
)
async def download_agent(
    request: Request,
    platform: str = Query("source", pattern="^(source|windows|macos)$",
                          description="source = mã Python | windows = VNMateAgent.exe | macos = bản macOS"),
    label: str = Query("", max_length=80, description="Tên gợi nhớ máy trạm (vd 'Kế toán - Lan')"),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Response:
    """
    Đóng gói động Client Agent thành file ZIP trên RAM (io.BytesIO).
    File config.json được tự động điền IP Master và ghi đè vào ZIP.
    Không tạo file .zip thừa trên ổ cứng máy chủ sau mỗi lần tải.
    """
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(
            status_code=403,
            detail="Chỉ Admin hoặc Manager có quyền tải Client Agent.",
        )

    # Phase 85: dựng địa chỉ máy chủ từ chính yêu cầu đang đến.
    #
    # Trước đây endpoint này đọc `PORT` trong `config.json` (đang là 443) rồi
    # ghi `wss://<ip>:443/ws/client` vào config.json của gói tải về. Gói đó KHÔNG
    # BAO GIỜ kết nối được: máy chủ thật chạy cổng 8000, và chạy HTTP thuần nên
    # `wss://` không bắt tay được. Người dùng tải Agent, chạy `python
    # agent.py`, agent báo lỗi kết nối liên tục — mà trên máy chủ mọi thứ vẫn
    # bình thường. Lỗi giống hệt mà `_local_worker_ws_url()` đã sửa từ trước,
    # nhưng bản đóng gói bị bỏ sót.
    #
    # Nay cả hai cùng đọc `request.url` (xem `_resolve_master_endpoint`), nên
    # IP/cổng/scheme luôn khớp với nơi người dùng đang xem trang này.
    scheme, host, port = _resolve_master_endpoint(request)

    # Nếu người dùng mở trang bằng localhost/loopback, máy con trong LAN không
    # nối được vào 127.0.0.1 — lúc đó mới thay bằng IP LAN của máy chủ.
    # Trong Docker, IP tự dò là IP của container (172.x) — máy trạm không tới được:
    # dùng VNMATEAI_PUBLIC_HOST (trình cài ghi IP LAN của máy chủ vào .env).
    server_ip = host
    if host in ("127.0.0.1", "localhost", "::1"):
        server_ip = os.environ.get("VNMATEAI_PUBLIC_HOST", "").strip() or _get_server_local_ip()

    default_port = 443 if scheme == "wss" else 80
    port_suffix = "" if port == default_port else f":{port}"
    ws_url = f"{scheme}://{server_ip}:{port}/ws/client"

    from mateai.application.devices import worker_enrollment
    enroll_code, enroll_info = worker_enrollment.create_enroll_code(
        created_by=str(current_user.get("username") or "unknown"), label=label)

    dynamic_config = {
        "server_url": f"{'https' if scheme == 'wss' else 'http'}://{server_ip}{port_suffix}",
        "ws_url": ws_url,
        "client_id": "auto_generate_on_first_run",
        "master_ip": server_ip,
        "master_port": port,
        "downloaded_at": datetime.utcnow().isoformat() + "Z",
        "downloaded_by": current_user.get("username", "unknown"),
        # Mã đăng ký DÙNG MỘT LẦN, riêng cho máy sẽ cài gói này (hết hạn sau 7
        # ngày). Trước đây là enrollment secret CHUNG của mọi máy: lộ một gói là
        # ai cũng nối được, và không thu hồi riêng một máy được.
        "enroll_code": enroll_code,
        "enroll_expires_at": enroll_info["expires_at"],
        "label": label,
    }

    cert_bytes = None
    try:
        from mateai.infrastructure.security.tls import CERT_FILE, ensure_ssl_certs
        ensure_ssl_certs()
        if CERT_FILE.exists() and CERT_FILE.stat().st_size > 0:
            cert_bytes = CERT_FILE.read_bytes()
    except Exception as cert_err:
        logger.error("Không đọc được chứng chỉ máy chủ để kèm vào gói Agent: %s", cert_err)

    from mateai.interfaces.http import agent_packages
    filename, zip_content, package = agent_packages.build_download(platform, dynamic_config, cert_bytes)

    logger.info(
        "Gói Agent (%s -> %s) tải bởi '%s', nhãn '%s', mã đăng ký %s, %d bytes",
        platform, package, current_user.get("username"), label, enroll_info["code_ref"], len(zip_content),
    )

    return Response(
        content=zip_content,
        media_type="application/x-zip-compressed",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Agent-Package": package,
            "Content-Length": str(len(zip_content)),
            "X-Agent-Server-IP": server_ip,
            "X-Agent-Version": agent_packages.latest_version(package) or "unknown",
            "X-Agent-Server-Port": str(port),
        },
    )
