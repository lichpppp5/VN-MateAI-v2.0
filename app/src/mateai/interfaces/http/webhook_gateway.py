# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/webhook_gateway.py
=======================
Webhook Receiver — Phase 59 Universal Enterprise Connector Hub.

Mở endpoint `POST /api/webhooks/{source}` để nhận cảnh báo (alerts) từ hệ thống
ngoại vi: AWS SNS, OCI Alarms, Paperless webhooks, eInvoice callbacks...

Luồng xử lý:
  1. External system đẩy JSON alert -> POST /api/webhooks/aws (hoặc oci, paperless, einvoice)
  2. WebhookGateway validate signature (nếu có), parse JSON
  3. Dịch sang tiếng Việt, enrich với context (severity, affected_resource)
  4. Đẩy vào **Proactive Agent** (Phase 56) -> AI cảnh báo CEO qua Telegram / ESP32 HUD / Voice.

Security:
  - Verify HMAC signature (AWS SNS, OCI, etc.)
  - Rate limiting per source
  - IP whitelist (optional)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from mateai.config.loader import settings
from mateai.application.skills.builtin.proactive_manager import proactive_manager
from mateai.application.security.zero_trust import log_security_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


# ================================================================
# Models
# ================================================================

class WebhookPayload(BaseModel):
    """Generic webhook payload - flexible cho mọi source."""
    source: str                                    # aws, oci, paperless, einvoice
    event_type: str                                # alarm, notification, webhook, callback
    severity: str = "info"                         # critical, high, medium, low, info
    title: str                                     # Tiêu đề ngắn
    message: str                                   # Chi tiết
    resource_id: Optional[str] = None              # ID tài nguyên bị ảnh hưởng (instance_id, doc_id, invoice_id...)
    resource_type: Optional[str] = None            # Loại: ec2_instance, rds_db, document, invoice...
    timestamp: Optional[str] = None                # ISO datetime
    raw_data: Dict[str, Any] = Field(default_factory=dict)  # Raw payload gốc
    metadata: Dict[str, Any] = Field(default_factory=dict)  # Metadata bổ sung


class WebhookResponse(BaseModel):
    """Response chuẩn cho webhook."""
    success: bool
    message: str
    alert_id: Optional[str] = None
    processed_at: str


# ================================================================
# Signature Verification
# ================================================================

class SignatureVerifier:
    """
    Xác thực chữ ký HMAC cho các provider phổ biến.
    """

    @staticmethod
    def verify_aws_sns(payload: bytes, signature: str, signing_cert_url: str) -> bool:
        """
        AWS SNS signature verification.
        Tham khảo: https://docs.aws.amazon.com/sns/latest/dg/sns-verify-signature.html
        """
        try:
            import httpx
            # Download certificate
            with httpx.Client(timeout=10.0) as client:
                cert_resp = client.get(signing_cert_url)
                if not cert_resp.is_success:
                    return False
                cert_pem = cert_resp.text

            # Verify signature (simplified - production nên dùng cryptography library)
            # AWS SNS uses SHA256withRSA
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.x509 import load_pem_x509_certificate

            cert = load_pem_x509_certificate(cert_pem.encode())
            public_key = cert.public_key()
            decoded_sig = __import__("base64").b64decode(signature)

            # String to sign: message + messageId + subject + timestamp + topicArn + type
            # Đây là version đơn giản - production cần parse message JSON và build string đúng format
            public_key.verify(
                decoded_sig,
                payload,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.warning("[WebhookGateway] AWS SNS signature verify failed: %s", e)
            return False

    @staticmethod
    def verify_hmac_sha256(payload: bytes, signature: str, secret: str) -> bool:
        """Generic HMAC-SHA256 verification."""
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def verify_oci_signature(payload: bytes, headers: Dict[str, str], secret: str) -> bool:
        """
        Xác thực chữ ký webhook OCI.

        Bản gốc trả `True` cho MỌI request có header `authorization` hoặc
        `x-oci-signature` — tức chỉ cần gõ bất kỳ chuỗi nào cũng được coi là
        đã xác thực. Đây không phải xác thực, đây là trang trí: ai cũng gửi
        được webhook giả và hệ thống tin là thật.

        OCI Notification dùng lược đồ RSA phức tạp (`keyId`, danh sách header
        được ký, chữ ký base64) và cần `oci-python-sdk` để kiểm chứng — không
        thể tái tạo đúng trong một hàm rời. Thay vì giả vờ làm được, hàm này
        kiểm tra HMAC-SHA256 trên thân message bằng shared secret, đúng cách
        các nguồn webhook tự tuỳ biến (Paperless, eInvoice) đang dùng.

        Trả `False` khi không xác minh được. Người gọi phải tôn trọng kết quả
        này, nếu không thì việc kiểm tra trở nên vô nghĩa.
        """
        if not secret:
            logger.debug("[WebhookGateway] OCI: chưa cấu hình secret, không xác thực được.")
            return False

        sig_header = (
            headers.get("x-oci-signature")
            or headers.get("x-vnmate-signature")
            or ""
        ).strip()
        if not sig_header:
            logger.debug("[WebhookGateway] OCI: thiếu header chữ ký.")
            return False

        if sig_header.startswith("sha256="):
            sig_header = sig_header[7:]

        # `hmac.compare_digest` chạy hằng thời gian -> không rò rỉ chữ ký đúng
        # qua thời gian phản hồi.
        ok = SignatureVerifier.verify_hmac_sha256(payload, sig_header, secret)
        if not ok:
            logger.warning("[WebhookGateway] OCI: HMAC không khớp — coi là webhook giả.")
        return ok


# ================================================================
# Alert Processing
# ================================================================

class AlertProcessor:
    """
    Xử lý alert: translate, enrich, dispatch to Proactive Manager & Telegram.
    """

    SEVERITY_VN = {
        "critical": "NGHIÊM TRỌNG",
        "high": "CAO",
        "medium": "TRUNG BÌNH",
        "low": "THẤP",
        "info": "THÔNG TIN",
    }

    SOURCE_VN = {
        "aws": "AWS (Amazon Web Services)",
        "oci": "OCI (Oracle Cloud)",
        "paperless": "Paperless-ngx (Quản trị tài liệu)",
        "einvoice": "Hóa đơn điện tử",
        "custom": "Hệ thống tùy chỉnh",
    }

    def __init__(self):
        self._alert_cache: Dict[str, float] = {}  # dedup: alert_key -> timestamp
        self._cache_ttl = 300  # 5 phút

    def _dedup_key(self, source: str, resource_id: str, event_type: str) -> str:
        return f"{source}:{resource_id}:{event_type}"

    def is_duplicate(self, source: str, resource_id: str, event_type: str) -> bool:
        """Deduplication đơn giản: không gửi alert trùng trong 5 phút."""
        key = self._dedup_key(source, resource_id, event_type)
        now = time.time()
        if key in self._alert_cache and (now - self._alert_cache[key]) < self._cache_ttl:
            return True
        self._alert_cache[key] = now
        return False

    def translate_to_vietnamese(self, payload: WebhookPayload) -> str:
        """Dịch alert sang tiếng Việt tự nhiên cho TTS/Telegram."""
        source_vn = self.SOURCE_VN.get(payload.source, payload.source)
        severity_vn = self.SEVERITY_VN.get(payload.severity.lower(), payload.severity.upper())

        resource_info = ""
        if payload.resource_id:
            resource_info = f" (Tài nguyên: {payload.resource_id}"
            if payload.resource_type:
                resource_info += f" - {payload.resource_type}"
            resource_info += ")"

        time_str = ""
        if payload.timestamp:
            try:
                dt = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
                time_str = f" lúc {dt.strftime('%H:%M:%S %d/%m/%Y')}"
            except Exception:
                time_str = f" lúc {payload.timestamp}"

        # Cảnh báo chưa xác thực phải nói ra ngay trên tin nhắn, không giấu
        # trong metadata mà CEO không đọc. Nếu không, một webhook giả mạo vẫn
        # hiện y hệt cảnh báo thật.
        trust_note = ""
        if not payload.metadata.get("verified", False):
            trust_note = "\n⚠️ _CHƯA XÁC THỰC — chưa cấu hình chữ ký webhook._"

        return (
            f"🚨 [{severity_vn}] Cảnh báo từ {source_vn}{resource_info}{time_str}\n"
            f"📋 {payload.title}\n"
            f"📝 {payload.message}{trust_note}"
        )

    async def dispatch(self, payload: WebhookPayload, alert_id: str) -> Dict[str, bool]:
        """
        Dispatch alert tới các kênh: Proactive Manager, Telegram, HUD.
        """
        results = {
            "proactive_manager": False,
            "alerts": False,
            "hud": False,
        }

        viet_text = self.translate_to_vietnamese(payload)

        # 1. Proactive Manager (Phase 56) - inject alert vào queue để AI xử lý
        try:
            # Ghi log sự kiện để Proactive Manager có thể xử lý trong chu kỳ sau
            # ProactiveManager không có inject_event, nên dùng logging + broadcast
            proactive_manager._audit_history.append({
                "event_type": f"external_alert_{payload.source}",
                "severity": payload.severity,
                "title": f"[Webhook] {payload.title}",
                "message": viet_text,
                "source": f"webhook:{payload.source}",
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": {
                    "alert_id": alert_id,
                    "source": payload.source,
                    "event_type": payload.event_type,
                    "resource_id": payload.resource_id,
                    "resource_type": payload.resource_type,
                    # Mang cờ này xuống hàng đợi Proactive Manager. Không có nó,
                    # mọi cảnh báo trông như nhau và người vận hành không phân
                    # biệt được webhook thật với webhook ai đó giả mạo.
                    "verified": payload.metadata.get("verified", False),
                    "raw_data": payload.raw_data,
                },
            })
            results["proactive_manager"] = True
            logger.info("[WebhookGateway] Alert %s logged for Proactive Manager", alert_id)
        except Exception as e:
            logger.error("[WebhookGateway] Proactive Manager dispatch failed: %s", e)

        # 2. Khâu cảnh báo chung (Telegram / Teams / Email / Outlook / Slack / Webhook)
        from mateai.application.operations import alert_dispatcher
        sev = str(payload.severity or "").lower()
        out = await alert_dispatcher.dispatch(
            payload.title or f"Cảnh báo từ {payload.source}", viet_text,
            severity="critical" if sev in ("critical", "high", "alarm", "error")
            else "warning" if sev in ("warning", "medium", "warn") else "info",
            category=f"webhook:{payload.source}:{payload.resource_id or payload.event_type}",
            source=f"Webhook {payload.source}")
        results["alerts"] = out.get("delivered", 0) > 0

        # 3. HUD WebSocket (broadcast to portal)
        try:
            from mateai.interfaces.websocket.realtime_hub import broadcast_hud
            import asyncio
            # Need running loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(broadcast_hud({
                    "type": "external_alert",
                    "alert_id": alert_id,
                    "source": payload.source,
                    "severity": payload.severity,
                    "title": payload.title,
                    "message": viet_text,
                    "resource_id": payload.resource_id,
                    "timestamp": datetime.utcnow().isoformat(),
                }))
                results["hud"] = True
            except RuntimeError:
                # No running loop
                pass
        except Exception as e:
            logger.error("[WebhookGateway] HUD broadcast failed: %s", e)

        return results


# Module-level instances
_verifier = SignatureVerifier()
_processor = AlertProcessor()

#: Số đo thật cho ô "Webhook" trên trang giám sát /admin/topology.
WEBHOOK_STATS: Dict[str, Any] = {"received": 0, "duplicates": 0, "last_at": None, "last_source": None}


# ================================================================
# FastAPI Routes
# ================================================================

@router.post(
    "/{source}",
    response_model=WebhookResponse,
    summary="Nhận webhook từ hệ thống ngoại vi (AWS, OCI, Paperless, eInvoice...)",
    description="""
    Endpoint chung cho tất cả webhook ngoại vi.
    
    Source hỗ trợ: aws, oci, paperless, einvoice, custom.
    
    Headers quan trọng:
    - X-Signature / X-Hub-Signature-256: HMAC signature (nếu có)
    - X-Amz-Sns-Message-Type: AWS SNS message type
    - X-Oci-Signature: OCI signature
    
    Body: JSON WebhookPayload
    """,
)
async def receive_webhook(
    source: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_amz_sns_message_type: Optional[str] = Header(None, alias="X-Amz-Sns-Message-Type"),
    x_oci_signature: Optional[str] = Header(None, alias="X-Oci-Signature"),
    signing_cert_url: Optional[str] = Header(None, alias="X-Amz-Sns-Signing-Cert-Url"),
) -> WebhookResponse:
    """
    Nhận webhook từ hệ thống ngoại vi.

    Args:
        source: Nguồn (aws, oci, paperless, einvoice, custom)
        request: Raw request để đọc body bytes cho signature verification
        background_tasks: FastAPI background tasks cho async processing

    Returns:
        WebhookResponse với alert_id để tracking.
    """
    # Validate source
    allowed_sources = ["aws", "oci", "paperless", "einvoice", "custom"]
    if source not in allowed_sources:
        raise HTTPException(status_code=400, detail=f"Unknown source '{source}'. Allowed: {allowed_sources}")

    # Read raw body
    body = await request.body()

    # Parse JSON
    try:
        json_data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    # ── Xác thực chữ ký ──────────────────────────────────────────────────
    # Bản gốc đặt `verified = True` rồi khi kiểm tra thất bại chỉ ghi log
    # "continuing anyway" và vẫn xử lý webhook. Nghĩa là bất kỳ ai cũng gửi
    # được cảnh báo giả (giả mạo "Instance đã chết", giả mạo "hóa đơn lỗi")
    # mà hệ thống tin và phát Telegram cho CEO.
    #
    # Nguyên tắc mới, nhất quán với phần còn lại của dự án:
    #   secret KHÔNG được cấu hình -> cho qua nhưng gắn cờ `verified: false`,
    #                                  để hệ thống chạy được ngay khi mới cài.
    #   secret ĐÃ được cấu hình + sai chữ ký -> TỪ CHỐI (401). Đã cam kết
    #                                  bảo mật thì không có đường vòng.
    verified = True
    secret_configured = False

    if source == "aws" and x_amz_sns_message_type:
        # AWS SNS: verify signature nếu có cert URL
        if signing_cert_url:
            secret_configured = True
            # Tải chứng chỉ ký của SNS bằng HTTP đồng bộ (tới 10 s) — ngoài event loop.
            from core.plugin_manager import run_blocking
            verified = await run_blocking(_verifier.verify_aws_sns, payload=body,
                                          signature=x_signature or "", signing_cert_url=signing_cert_url)
            if not verified:
                logger.warning("[WebhookGateway] AWS SNS signature verification FAILED for %s", source)
    elif source in ("paperless", "einvoice", "custom"):
        # HMAC-SHA256 với shared secret từ config/env
        secret = os.getenv(f"VNMATE_WEBHOOK_{source.upper()}_SECRET", "")
        if secret:
            secret_configured = True
            if x_signature or x_hub_signature_256:
                sig = x_hub_signature_256 or x_signature
                if sig.startswith("sha256="):
                    sig = sig[7:]
                verified = _verifier.verify_hmac_sha256(body, sig, secret)
            else:
                # Đã cấu hình secret mà request không mang chữ ký: coi như giả.
                verified = False
            if not verified:
                logger.warning("[WebhookGateway] HMAC verification FAILED for %s", source)
    elif source == "oci":
        secret = os.getenv("VNMATE_WEBHOOK_OCI_SECRET", "")
        if secret:
            secret_configured = True
            verified = _verifier.verify_oci_signature(body, dict(request.headers), secret)
            if not verified:
                logger.warning("[WebhookGateway] OCI signature verification FAILED for %s", source)

    if secret_configured and not verified:
        raise HTTPException(
            status_code=401,
            detail=(
                f"Webhook '{source}' bị từ chối: chữ ký không hợp lệ. "
                "Kiểm tra lại VNMATE_WEBHOOK_SECRET và nội dung chữ ký gửi đi."
            ),
        )

    if not secret_configured:
        # Mặc định TỪ CHỐI: endpoint này không cần đăng nhập, nên webhook không chữ
        # ký = ai cũng đẩy được nội dung tuỳ ý vào nhóm Telegram admin và HUD.
        # Chỉ nhận khi admin bật tường minh `security.allow_unsigned_webhooks`
        # (giai đoạn cài đặt ban đầu).
        from mateai.config.loader import get_config_section
        if not get_config_section("security").get("allow_unsigned_webhooks", False):
            logger.warning("[WebhookGateway] Từ chối webhook '%s' không chữ ký (chưa cấu hình secret).", source)
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Webhook '{source}' bị từ chối: chưa cấu hình chữ ký. Đặt "
                    f"VNMATE_WEBHOOK_{source.upper()}_SECRET (hoặc tạm bật "
                    "security.allow_unsigned_webhooks khi cài đặt)."
                ),
            )
        # Được phép nhận không chữ ký: vẫn nhận nhưng đặt
        # `verified = False` để payload và log không bao giờ tuyên bố sai là
        # đã xác thực. Giữ nguyên `verified` ở đây sẽ khiến mọi lần kiểm tra
        # về sau tin vào một thứ chưa từng được kiểm tra.
        verified = False
        logger.warning(
            "[WebhookGateway] Webhook '%s' KHÔNG được xác thực (chưa cấu hình secret) — "
            "vẫn xử lý nhưng không phải nguồn tin cậy.",
            source,
        )

    # Build payload
    payload = WebhookPayload(
        source=source,
        event_type=json_data.get("event_type", json_data.get("Type", "notification")),
        severity=json_data.get("severity", json_data.get("Severity", "info")),
        title=json_data.get("title", json_data.get("Subject", "Cảnh báo hệ thống ngoại vi")),
        message=json_data.get("message", json_data.get("Message", json.dumps(json_data, ensure_ascii=False)[:500])),
        resource_id=json_data.get("resource_id", json_data.get("AlarmName", json_data.get("resourceId"))),
        resource_type=json_data.get("resource_type", json_data.get("ResourceType")),
        timestamp=json_data.get("timestamp", json_data.get("Timestamp", json_data.get("StateChangeTime"))),
        raw_data=json_data,
        metadata={
            "verified": verified,
            "headers": dict(request.headers),
            "client_ip": request.client.host if request.client else "unknown",
        },
    )

    # Generate alert ID
    alert_id = f"WH-{source.upper()}-{int(time.time() * 1000) % 1000000:06d}"

    # Deduplication check
    from mateai.application.operations.topology_events import emit
    WEBHOOK_STATS["received"] += 1
    WEBHOOK_STATS["last_at"] = time.time()
    WEBHOOK_STATS["last_source"] = source
    if _processor.is_duplicate(source, payload.resource_id or "unknown", payload.event_type):
        WEBHOOK_STATS["duplicates"] += 1
        emit("webhook", stage="duplicate", node="webhook", status="cancelled",
             detail=f"{source}: {payload.event_type} (trùng, bỏ qua)")
        logger.info("[WebhookGateway] Duplicate alert suppressed: %s", alert_id)
        return WebhookResponse(
            success=True,
            message="Duplicate alert suppressed",
            alert_id=alert_id,
            processed_at=datetime.utcnow().isoformat(),
        )

    # Log audit
    log_security_audit(
        client_id=f"webhook:{source}",
        action="WEBHOOK_RECEIVED",
        risk="INFO",
        status="RECEIVED" if verified else "UNVERIFIED",
        details={"alert_id": alert_id, "source": source, "event_type": payload.event_type},
    )

    emit("webhook", stage="received", source="webhook", target="alerts", status="ok",
         detail=f"{alert_id} · {source}: {payload.event_type}")

    # Dispatch in background
    background_tasks.add_task(_processor.dispatch, payload, alert_id)

    return WebhookResponse(
        success=True,
        message="Webhook received and queued for processing",
        alert_id=alert_id,
        processed_at=datetime.utcnow().isoformat(),
    )


@router.get(
    "/health",
    summary="Health check cho webhook gateway",
)
async def webhook_health() -> Dict[str, Any]:
    """Kiểm tra webhook gateway hoạt động."""
    return {
        "status": "healthy",
        "service": "Webhook Gateway",
        "supported_sources": ["aws", "oci", "paperless", "einvoice", "custom"],
        "timestamp": datetime.utcnow().isoformat(),
    }


# ================================================================
# Convenience: Register router vào FastAPI app
# ================================================================

def register_webhook_routes(app) -> None:
    """Gọi trong server.py startup để mount webhook routes."""
    app.include_router(router)
    logger.info("[WebhookGateway] Routes registered at /api/webhooks/{source}")