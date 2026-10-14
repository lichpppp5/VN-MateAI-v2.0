# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/m365_connector.py
=================================
Microsoft 365 Connector — Phase Omnichannel Enterprise Communication.
Sử dụng Microsoft Graph API theo chuẩn OAuth2 Client Credentials Flow (App-Only Daemon).
Hỗ trợ:
  1. Gửi tin nhắn cảnh báo/nhiệm vụ vào kênh Microsoft Teams: send_teams_channel_message
  2. Gửi email tổng hợp điều hành từ hòm thư hệ thống qua Microsoft Graph: send_outlook_email
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional
import httpx

from mateai.config.loader import settings

logger = logging.getLogger("mateai.infrastructure.connectors.m365")


class Microsoft365Connector:
    """Connector giao tiếp trực tiếp với hệ sinh thái Microsoft 365."""

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        system_email: Optional[str] = None,
    ) -> None:
        self.tenant_id = tenant_id or os.getenv("M365_TENANT_ID") or getattr(settings, "M365_TENANT_ID", "")
        self.client_id = client_id or os.getenv("M365_CLIENT_ID") or getattr(settings, "M365_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("M365_CLIENT_SECRET") or getattr(settings, "M365_CLIENT_SECRET", "")
        self.system_email = system_email or os.getenv("M365_SYSTEM_EMAIL") or getattr(settings, "M365_SYSTEM_EMAIL", "assistant@vnmate.ai")

        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        """Kiểm tra cấu hình Microsoft 365 đã khai báo đầy đủ chưa."""
        return bool(self.tenant_id and self.client_id and self.client_secret)

    async def get_access_token(self) -> Optional[str]:
        """Lấy Bearer Access Token qua OAuth2 Client Credentials Flow."""
        if not self.is_configured:
            return None

        if self._cached_token and time.time() < self._token_expires_at:
            return self._cached_token

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(token_url, data=data)
                if resp.status_code == 200:
                    payload = resp.json()
                    self._cached_token = payload.get("access_token")
                    expires_in = payload.get("expires_in", 3600)
                    self._token_expires_at = time.time() + expires_in - 120
                    return self._cached_token
                else:
                    logger.warning("[M365] Lỗi lấy token (%s): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("[M365] Không thể kết nối Microsoft Online Login: %s", e)

        return None

    def _not_sent(self, channel: str, destination: str, subject: str) -> Dict[str, Any]:
        reason = ("chưa cấu hình M365_TENANT_ID / M365_CLIENT_ID / M365_CLIENT_SECRET"
                  if not self.is_configured else "không lấy được access token từ Microsoft Entra ID")
        logger.warning("[M365 %s] KHÔNG gửi tới %s (%s): %s", channel, destination, reason, subject)
        return {"status": "error", "code": "not_sent", "detail": reason, "destination": destination}

    async def send_teams_channel_message(
        self,
        team_id: str,
        channel_id: str,
        subject: str,
        body_html: str,
    ) -> Dict[str, Any]:
        """Bắn cảnh báo, thông báo trực tiếp vào Channel Microsoft Teams."""
        token = await self.get_access_token()
        if not token:
            # Trước đây trả "success" (mode "simulated") — người gọi tưởng cảnh báo đã
            # tới Teams trong khi không có gì được gửi.
            return self._not_sent("Teams", f"teams://{team_id}/{channel_id}", subject)

        url = f"https://graph.microsoft.com/v1.0/teams/{team_id}/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "subject": subject,
            "body": {
                "contentType": "html",
                "content": body_html,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    return {"status": "success", "mode": "direct", "response": resp.json()}
                else:
                    logger.error("[M365 Teams] Gửi thất bại (%s): %s", resp.status_code, resp.text)
                    return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except Exception as e:
            logger.error("[M365 Teams] Lỗi mạng khi gửi: %s", e)
            return {"status": "error", "detail": str(e)}

    async def send_outlook_email(
        self,
        to_recipients: List[str],
        subject: str,
        body_html: str,
    ) -> Dict[str, Any]:
        """Gửi email điều hành từ hòm thư hệ thống qua Microsoft Graph API."""
        token = await self.get_access_token()
        if not token:
            return self._not_sent("Outlook", ", ".join(to_recipients), subject)

        url = f"https://graph.microsoft.com/v1.0/users/{self.system_email}/sendMail"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        recipients_list = [{"emailAddress": {"address": r.strip()}} for r in to_recipients if r.strip()]
        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html,
                },
                "toRecipients": recipients_list,
            },
            "saveToSentItems": "true",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 202):
                    return {"status": "success", "mode": "direct"}
                else:
                    logger.error("[M365 Outlook] Gửi email thất bại (%s): %s", resp.status_code, resp.text)
                    return {"status": "error", "code": resp.status_code, "detail": resp.text}
        except Exception as e:
            logger.error("[M365 Outlook] Lỗi gửi email: %s", e)
            return {"status": "error", "detail": str(e)}


# Singleton instance
m365_connector = Microsoft365Connector()
