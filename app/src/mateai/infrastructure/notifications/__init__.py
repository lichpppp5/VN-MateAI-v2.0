# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/notifications
===================================
Kênh gửi cảnh báo ra phần mềm doanh nghiệp: Microsoft Teams, Email (SMTP),
Outlook (Microsoft 365 Graph), Slack, Webhook tuỳ chỉnh (+ Telegram có sẵn).

Mỗi kênh "chờ kết nối" cho tới khi điền đủ khoá bắt buộc trong config.json (form
ở Portal → Cấu hình kết nối ngoại vi) hoặc biến môi trường — xem
docs/integrations/alert-channels.md. Điều phối: application/operations/alert_dispatcher.
"""
from mateai.infrastructure.notifications.channels import (  # noqa: F401
    CHANNELS,
    RULES_ID,
    channel_ids,
    config_schema,
    load_rules,
    load_settings,
    missing_fields,
    send,
)
