# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/notifications/channels.py
===============================================
Khai báo + cách gửi của từng kênh cảnh báo.

Cấu hình (ưu tiên cao -> thấp): biến môi trường `<KÊNH>_<TRƯỜNG>` (vd
`ALERT_SMTP_PASSWORD`) > khối `<kênh>` trong config.json > mặc định. Không có
khoá thật nào trong code. Kênh chỉ chạy khi đủ khoá BẮT BUỘC và `enabled` khác
false; thiếu thì là "chờ kết nối" — không bao giờ báo "đã gửi" khi chưa gửi.

`send()` trả `(ok, chi_tiết)`: ok=True chỉ khi phía nhận đã xác nhận (HTTP 2xx /
SMTP chấp nhận). Chi tiết lỗi KHÔNG chứa URL webhook hay mật khẩu (URL webhook
Teams / Slack chính là khoá bí mật).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

SEND_TIMEOUT_S = 15.0
SEVERITIES = ("info", "warning", "critical")
SEVERITY_TEXT = {"info": "THÔNG TIN", "warning": "CẢNH BÁO", "critical": "NGHIÊM TRỌNG"}
SEVERITY_ICON = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}

#: Quy tắc chung (không phải một kênh): mức tối thiểu, chống lặp, theo dõi sơ đồ.
RULES_ID = "alert_rules"

# Mỗi trường: (mặc định, nhãn, mô tả, bí_mật)
_F = Tuple[Any, str, str, bool]

CHANNELS: Dict[str, Dict[str, Any]] = {
    "alert_teams": {
        "display_name": "Microsoft Teams",
        "description": "Đăng cảnh báo vào kênh Teams qua Workflows webhook (Adaptive Card).",
        "node": "notify:teams",
        "required": ("webhook_url",),
        "fields": {
            "webhook_url": ("", "Webhook URL", "Teams → kênh → Workflows → 'Post to a channel when a webhook request is received' → sao chép URL.", True),
            "enabled": (True, "Bật kênh", "", False),
            "min_severity": ("", "Mức tối thiểu", "info / warning / critical — để trống: theo quy tắc chung.", False),
        },
    },
    "alert_email": {
        "display_name": "Email (SMTP)",
        "description": "Gửi email cảnh báo qua máy chủ SMTP của doanh nghiệp (Exchange, Gmail, Zimbra…).",
        "node": "notify:email",
        "required": ("smtp_host", "from_address", "to_addresses"),
        "fields": {
            "smtp_host": ("", "Máy chủ SMTP", "vd smtp.office365.com, smtp.gmail.com, mail.congty.vn", False),
            "smtp_port": (587, "Cổng", "587 (STARTTLS) hoặc 465 (SSL)", False),
            "username": ("", "Tài khoản", "Để trống nếu máy chủ cho relay không xác thực.", False),
            "password": ("", "Mật khẩu / App password", "", True),
            "from_address": ("", "Địa chỉ gửi", "vd canhbao@congty.vn", False),
            "to_addresses": ("", "Người nhận", "Nhiều địa chỉ cách nhau dấu phẩy.", False),
            "use_ssl": (False, "SSL trực tiếp (465)", "Bật khi dùng cổng 465; tắt để dùng STARTTLS.", False),
            "enabled": (True, "Bật kênh", "", False),
            "min_severity": ("", "Mức tối thiểu", "info / warning / critical — để trống: theo quy tắc chung.", False),
        },
    },
    "alert_outlook": {
        "display_name": "Outlook (Microsoft 365)",
        "description": "Gửi mail từ hộp thư hệ thống qua Microsoft Graph (ứng dụng Entra ID, quyền Mail.Send).",
        "node": "notify:outlook",
        "required": ("tenant_id", "client_id", "client_secret", "sender_mailbox", "to_addresses"),
        "fields": {
            "tenant_id": ("", "Tenant ID", "Microsoft Entra ID → Overview → Directory (tenant) ID", False),
            "client_id": ("", "Client ID", "App registration → Application (client) ID", False),
            "client_secret": ("", "Client secret", "App registration → Certificates & secrets", True),
            "sender_mailbox": ("", "Hộp thư gửi", "vd canhbao@congty.onmicrosoft.com", False),
            "to_addresses": ("", "Người nhận", "Nhiều địa chỉ cách nhau dấu phẩy.", False),
            "enabled": (True, "Bật kênh", "", False),
            "min_severity": ("", "Mức tối thiểu", "info / warning / critical — để trống: theo quy tắc chung.", False),
        },
    },
    "alert_slack": {
        "display_name": "Slack",
        "description": "Đăng cảnh báo vào kênh Slack qua Incoming Webhook.",
        "node": "notify:slack",
        "required": ("webhook_url",),
        "fields": {
            "webhook_url": ("", "Webhook URL", "Slack App → Incoming Webhooks → Add New Webhook to Workspace.", True),
            "enabled": (True, "Bật kênh", "", False),
            "min_severity": ("", "Mức tối thiểu", "info / warning / critical — để trống: theo quy tắc chung.", False),
        },
    },
    "alert_webhook": {
        "display_name": "Webhook tuỳ chỉnh",
        "description": "POST JSON tới hệ thống bất kỳ (ITSM, Jira/ServiceNow automation, Zalo OA qua middleware…).",
        "node": "notify:webhook",
        "required": ("webhook_url",),
        "fields": {
            "webhook_url": ("", "URL nhận", "Nhận POST JSON {title, message, severity, category, source, resolved, time}.", True),
            "hmac_secret": ("", "Khoá ký HMAC", "Tuỳ chọn: thêm header X-VNMate-Signature: sha256=<hex>.", True),
            "enabled": (True, "Bật kênh", "", False),
            "min_severity": ("", "Mức tối thiểu", "info / warning / critical — để trống: theo quy tắc chung.", False),
        },
    },
}

RULES_FIELDS: Dict[str, _F] = {
    "min_severity": ("warning", "Mức tối thiểu gửi đi", "info / warning / critical", False),
    "cooldown_s": (300, "Chống lặp (giây)", "Cùng một sự cố không gửi lại trong khoảng này.", False),
    "watch_topology": (True, "Theo dõi sơ đồ hệ thống", "Thành phần chuyển sang Lỗi đủ lâu -> gửi cảnh báo; hồi phục -> báo đã khôi phục.", False),
    "down_after_s": (30, "Lỗi kéo dài (giây) mới báo", "Tránh báo nhầm khi chập chờn.", False),
    "alert_on_degraded": (False, "Báo cả khi Suy giảm", "Mặc định chỉ báo khi Lỗi.", False),
    "ignore_nodes": ("voice,hud,portal", "Bỏ qua thành phần", "id trên sơ đồ, cách nhau dấu phẩy.", False),
}


def channel_ids() -> List[str]:
    return list(CHANNELS)


def _fields(cid: str) -> Dict[str, _F]:
    return RULES_FIELDS if cid == RULES_ID else CHANNELS[cid]["fields"]


def _coerce(default: Any, raw: Any) -> Any:
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on", "bật")
    if isinstance(default, int):
        try:
            return int(str(raw).strip())
        except ValueError:
            return default
    return "" if raw is None else str(raw).strip()


def load_settings(cid: str) -> Dict[str, Any]:
    """Mặc định < config.json (khối `cid`) < biến môi trường `CID_FIELD`. Đọc mới mỗi lần."""
    from mateai.config.loader import get_config_section
    block = get_config_section(cid)
    out: Dict[str, Any] = {}
    for key, (default, *_rest) in _fields(cid).items():
        raw = os.getenv(f"{cid.upper()}_{key.upper()}")
        if raw in (None, ""):
            raw = block.get(key, default)
        out[key] = _coerce(default, raw) if raw not in (None, "") else default
    return out


def load_rules() -> Dict[str, Any]:
    r = load_settings(RULES_ID)
    r["ignore_nodes"] = [x.strip() for x in str(r.get("ignore_nodes") or "").split(",") if x.strip()]
    if r.get("min_severity") not in SEVERITIES:
        r["min_severity"] = "warning"
    return r


def missing_fields(cid: str, settings: Optional[Dict[str, Any]] = None) -> List[str]:
    """TÊN khoá bắt buộc còn thiếu (không bao giờ trả giá trị)."""
    if cid == RULES_ID:
        return []
    s = settings if settings is not None else load_settings(cid)
    return [k for k in CHANNELS[cid]["required"] if not str(s.get(k) or "").strip()]


def config_schema(cid: str) -> Dict[str, Any]:
    """JSON Schema cho form Portal (cùng định dạng connector: format=secret -> ô mật khẩu)."""
    required = () if cid == RULES_ID else CHANNELS[cid]["required"]
    props: Dict[str, Any] = {}
    for key, (default, title, desc, secret) in _fields(cid).items():
        prop: Dict[str, Any] = {
            "type": "boolean" if isinstance(default, bool) else ("number" if isinstance(default, int) else "string"),
            "title": title,
        }
        if desc:
            prop["description"] = desc
        if secret:
            prop["format"] = "secret"
        if default not in ("", None):
            prop["default"] = default
        props[key] = prop
    return {"type": "object", "properties": props, "required": list(required)}


# ── Định dạng ────────────────────────────────────────────────────────────────

def _headline(alert: Dict[str, Any]) -> str:
    if alert.get("resolved"):
        return f"✅ [ĐÃ KHÔI PHỤC] {alert['title']}"
    sev = alert.get("severity", "warning")
    return f"{SEVERITY_ICON.get(sev, '⚠️')} [{SEVERITY_TEXT.get(sev, sev.upper())}] {alert['title']}"


def _when(alert: Dict[str, Any]) -> str:
    return datetime.fromtimestamp(alert.get("time") or 0).strftime("%H:%M:%S %d/%m/%Y")


def format_text(alert: Dict[str, Any]) -> str:
    return (f"{_headline(alert)}\n\n{alert.get('message') or ''}\n\n"
            f"Nguồn: {alert.get('source') or 'VN-MateAI'} · {_when(alert)}")


def format_telegram_html(alert: Dict[str, Any]) -> str:
    return (f"<b>{html.escape(_headline(alert))}</b>\n\n{html.escape(alert.get('message') or '')}\n\n"
            f"<i>Nguồn: {html.escape(alert.get('source') or 'VN-MateAI')} · {_when(alert)}</i>")


def _teams_payload(alert: Dict[str, Any]) -> Dict[str, Any]:
    color = "Good" if alert.get("resolved") else {"critical": "Attention", "warning": "Warning"}.get(
        alert.get("severity"), "Default")
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": _headline(alert), "weight": "Bolder", "size": "Medium",
             "color": color, "wrap": True},
            {"type": "TextBlock", "text": alert.get("message") or "", "wrap": True},
            {"type": "FactSet", "facts": [
                {"title": "Mức độ", "value": "Đã khôi phục" if alert.get("resolved")
                 else SEVERITY_TEXT.get(alert.get("severity"), "")},
                {"title": "Nguồn", "value": alert.get("source") or "VN-MateAI"},
                {"title": "Thời gian", "value": _when(alert)},
            ]},
        ],
    }
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def _addresses(raw: Any) -> List[str]:
    return [a.strip() for a in str(raw or "").replace(";", ",").split(",") if a.strip()]


# ── Gửi ─────────────────────────────────────────────────────────────────────

async def _post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
                     body: Optional[bytes] = None) -> Tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_S) as client:
            if body is not None:
                resp = await client.post(url, content=body, headers={"Content-Type": "application/json", **(headers or {})})
            else:
                resp = await client.post(url, json=payload, headers=headers or {})
    except httpx.HTTPError as exc:
        return False, f"lỗi mạng ({type(exc).__name__})"
    if 200 <= resp.status_code < 300:
        return True, f"HTTP {resp.status_code}"
    return False, f"bị từ chối HTTP {resp.status_code}: {resp.text[:120]}"


async def _send_teams(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    return await _post_json(s["webhook_url"], _teams_payload(alert))


async def _send_slack(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    return await _post_json(s["webhook_url"], {"text": format_text(alert)})


async def _send_webhook(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    data = {k: alert.get(k) for k in ("title", "message", "severity", "category", "source", "resolved")}
    data["time"] = datetime.fromtimestamp(alert.get("time") or 0).isoformat(timespec="seconds")
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = {}
    if s.get("hmac_secret"):
        sig = hmac.new(str(s["hmac_secret"]).encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-VNMate-Signature"] = f"sha256={sig}"
    return await _post_json(s["webhook_url"], data, headers=headers, body=body)


def _smtp_send_blocking(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    to = _addresses(s.get("to_addresses"))
    if not to:
        return False, "chưa có người nhận"
    msg = EmailMessage()
    msg["Subject"] = f"[VN-MateAI] {_headline(alert)}"
    msg["From"] = s["from_address"]
    msg["To"] = ", ".join(to)
    msg.set_content(format_text(alert))
    port = int(s.get("smtp_port") or (465 if s.get("use_ssl") else 587))
    ctx = ssl.create_default_context()
    try:
        if s.get("use_ssl"):
            server = smtplib.SMTP_SSL(s["smtp_host"], port, timeout=SEND_TIMEOUT_S, context=ctx)
        else:
            server = smtplib.SMTP(s["smtp_host"], port, timeout=SEND_TIMEOUT_S)
        with server:
            if not s.get("use_ssl"):
                server.ehlo()
                if server.has_extn("starttls"):
                    server.starttls(context=ctx)
                    server.ehlo()
            if s.get("username"):
                server.login(s["username"], s.get("password") or "")
            refused = server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, "sai tài khoản / mật khẩu SMTP"
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"SMTP lỗi ({type(exc).__name__}: {str(exc)[:80]})"
    if refused:
        return False, f"máy chủ từ chối {len(refused)}/{len(to)} người nhận"
    return True, f"đã gửi tới {len(to)} người nhận"


async def _send_email(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    return await asyncio.to_thread(_smtp_send_blocking, s, alert)


_OUTLOOK_CLIENTS: Dict[Tuple[str, str, str, str], Any] = {}


async def _send_outlook(s: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    from mateai.infrastructure.connectors.m365_connector import Microsoft365Connector
    key = (s["tenant_id"], s["client_id"], s["client_secret"], s["sender_mailbox"])
    client = _OUTLOOK_CLIENTS.get(key)
    if client is None:   # giữ token Graph giữa các lần gửi
        client = _OUTLOOK_CLIENTS[key] = Microsoft365Connector(
            tenant_id=s["tenant_id"], client_id=s["client_id"], client_secret=s["client_secret"],
            system_email=s["sender_mailbox"])
    to = _addresses(s.get("to_addresses"))
    body_html = html.escape(format_text(alert)).replace("\n", "<br>")
    res = await client.send_outlook_email(to, f"[VN-MateAI] {_headline(alert)}", body_html)
    if res.get("status") == "success":
        return True, f"đã gửi tới {len(to)} người nhận"
    return False, str(res.get("detail") or res.get("code") or "Graph từ chối")[:160]


_SENDERS = {
    "alert_teams": _send_teams,
    "alert_email": _send_email,
    "alert_outlook": _send_outlook,
    "alert_slack": _send_slack,
    "alert_webhook": _send_webhook,
}


async def send(cid: str, settings: Dict[str, Any], alert: Dict[str, Any]) -> Tuple[bool, str]:
    """Gửi một cảnh báo qua kênh `cid`. Không ném lỗi; quá hạn -> (False, 'quá thời gian')."""
    try:
        return await asyncio.wait_for(_SENDERS[cid](settings, alert), timeout=SEND_TIMEOUT_S + 5)
    except asyncio.TimeoutError:
        return False, "quá thời gian chờ phản hồi"
    except Exception as exc:  # noqa: BLE001 — một kênh hỏng không làm hỏng các kênh khác
        logger.warning("[Alerts] kênh %s lỗi: %s", cid, type(exc).__name__)
        return False, f"lỗi {type(exc).__name__}"
