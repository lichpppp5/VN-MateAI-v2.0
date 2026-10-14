# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/aws_connector.py
================================
AWS Adapter — Phase 59 Universal Enterprise Connector Hub.

Sử dụng `boto3` để kết nối AWS.
Cung cấp các method:
  - get_billing_summary(): Chi phí AWS trong tháng (Cost Explorer API).
  - get_instance_status(): Danh sách EC2 đang chạy (EC2 API).

Yêu cầu cài đặt:
  pip install boto3

Cấu hình (ENV VARS / config.json):
  AWS_REGION (mặc định: ap-southeast-1)
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_COST_EXPLORER_ENABLED (true/false - Cost Explorer cần bật riêng)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from mateai.infrastructure.connectors.base_connector import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    load_connector_settings,
)

logger = logging.getLogger(__name__)


class AWSConnector(BaseConnector):
    """
    Adapter kết nối AWS (boto3).

    Lưu ý: Cost Explorer API cần bật riêng trong AWS Console và có độ trễ ~24h.
    """

    def __init__(self, config: Optional[ConnectorConfig] = None):
        if config is None:
            config = ConnectorConfig.from_settings("aws", load_connector_settings("aws"))
        super().__init__(config)
        self._ce_client = None  # Cost Explorer
        self._ec2_client = None  # EC2
        self._sts_client = None  # STS (verify identity)

    def _reset_cached_clients(self) -> None:
        """Key đổi thì client boto3 cũ (đã khoá credential) không dùng lại được."""
        self._ce_client = None
        self._ec2_client = None
        self._sts_client = None

    # ------------------------------------------------------------------
    # Authentication & Client Initialization
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Khởi tạo boto3 clients. Verify credentials qua STS GetCallerIdentity."""
        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError

            region = self.config.extra.get("region", "ap-southeast-1")

            # boto3 tự động đọc AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY từ env
            session = boto3.Session(region_name=region)

            # Verify credentials
            self._sts_client = session.client("sts")
            identity = self._sts_client.get_caller_identity()
            account_id = identity.get("Account", "unknown")
            arn = identity.get("Arn", "unknown")
            logger.info("[AWSConnector] Authenticated as Account=%s, ARN=%s", account_id, self._mask_secrets(arn))

            # Init service clients
            self._ce_client = session.client("ce")  # Cost Explorer
            self._ec2_client = session.client("ec2")

            self._authenticated = True
            self._last_auth_time = __import__("time").time()
            self._token_expires_at = self._last_auth_time + 3600  # STS credentials thường 1h
            return True

        except NoCredentialsError:
            logger.error("[AWSConnector] No AWS credentials found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.")
            self._authenticated = False
            return False
        except Exception as e:
            logger.error("[AWSConnector] Authentication failed: %s", e, exc_info=True)
            self._authenticated = False
            return False

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> ConnectorResult:
        """Ping STS GetCallerIdentity để verify AWS reachable."""
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Authentication failed", source=self.config.name)
        try:
            t0 = __import__("time").time()
            self._sts_client.get_caller_identity()
            latency = (__import__("time").time() - t0) * 1000
            return ConnectorResult(
                success=True,
                data={"status": "healthy", "service": "AWS STS"},
                latency_ms=latency,
                source=self.config.name,
            )
        except Exception as e:
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    # ------------------------------------------------------------------
    # Business Methods
    # ------------------------------------------------------------------

    async def get_billing_summary(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        granularity: str = "MONTHLY",
        group_by: Optional[List[str]] = None,
    ) -> ConnectorResult:
        """
        Lấy tổng chi phí AWS trong khoảng thời gian (Cost Explorer).

        Args:
            start_date: ISO date (YYYY-MM-DD). Mặc định: 1 tháng trước.
            end_date: ISO date. Mặc định: hôm nay.
            granularity: "DAILY" | "MONTHLY" | "HOURLY"
            group_by: List dimension để group (ví dụ: ["SERVICE", "LINKED_ACCOUNT"]).

        Returns:
            ConnectorResult với data = {
                "total_cost_usd": float,
                "currency": "USD",
                "period": {"start": ..., "end": ...},
                "breakdown": [{"service": "...", "amount": ..., "unit": "USD"}, ...],
                "raw": [...]  # raw response từ Cost Explorer
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        if not self.config.extra.get("cost_explorer_enabled", True):
            return ConnectorResult(
                success=False,
                error="Cost Explorer is disabled in config. Set AWS_COST_EXPLORER_ENABLED=true.",
                source=self.config.name,
            )

        # Default: 30 days back
        if not end_date:
            end_date = datetime.utcnow().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

        group_defs = [{"Type": "DIMENSION", "Key": k} for k in (group_by or ["SERVICE"])]

        try:
            t0 = __import__("time").time()
            response = self._ce_client.get_cost_and_usage(
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity=granularity,
                Metrics=["UnblendedCost"],
                GroupBy=group_defs,
            )
            latency = (__import__("time").time() - t0) * 1000

            # Parse response
            total = 0.0
            breakdown = []
            for result_by_time in response.get("ResultsByTime", []):
                for group in result_by_time.get("Groups", []):
                    keys = group.get("Keys", [])
                    metrics = group.get("Metrics", {})
                    amount = float(metrics.get("UnblendedCost", {}).get("Amount", 0))
                    total += amount
                    breakdown.append({
                        "dimension": keys[0] if keys else "Unknown",
                        "amount": amount,
                        "unit": metrics.get("UnblendedCost", {}).get("Unit", "USD"),
                    })

            return ConnectorResult(
                success=True,
                data={
                    "total_cost_usd": round(total, 2),
                    "currency": "USD",
                    "period": {"start": start_date, "end": end_date},
                    "granularity": granularity,
                    "breakdown": sorted(breakdown, key=lambda x: x["amount"], reverse=True),
                    "raw": response,
                },
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[AWSConnector] get_billing_summary failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def get_instance_status(
        self,
        state_filter: Optional[List[str]] = None,
        tag_filters: Optional[Dict[str, str]] = None,
    ) -> ConnectorResult:
        """
        Lấy danh sách EC2 instances.

        Args:
            state_filter: List state để lọc (ví dụ: ["running", "stopped"]). Mặc định: ["running"].
            tag_filters: Dict tag key-value để lọc (ví dụ: {"Environment": "prod"}).

        Returns:
            ConnectorResult với data = {
                "total_instances": int,
                "instances": [
                    {"instance_id": "i-xxx", "state": "running", "type": "t3.medium",
                     "private_ip": "...", "public_ip": "...", "tags": {...}, "launch_time": "..."},
                    ...
                ]
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        state_filter = state_filter or ["running"]

        try:
            t0 = __import__("time").time()

            filters = [{"Name": "instance-state-name", "Values": state_filter}]
            if tag_filters:
                for k, v in tag_filters.items():
                    filters.append({"Name": f"tag:{k}", "Values": [v]})

            paginator = self._ec2_client.get_paginator("describe_instances")
            instances = []

            for page in paginator.paginate(Filters=filters):
                for reservation in page.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])} if inst.get("Tags") else {}
                        instances.append({
                            "instance_id": inst.get("InstanceId"),
                            "state": inst.get("State", {}).get("Name"),
                            "type": inst.get("InstanceType"),
                            "private_ip": inst.get("PrivateIpAddress"),
                            "public_ip": inst.get("PublicIpAddress"),
                            "availability_zone": inst.get("Placement", {}).get("AvailabilityZone"),
                            "launch_time": inst.get("LaunchTime").isoformat() if inst.get("LaunchTime") else None,
                            "tags": tags,
                            "platform": inst.get("PlatformDetails") or "Linux/UNIX",
                        })

            latency = (__import__("time").time() - t0) * 1000
            return ConnectorResult(
                success=True,
                data={
                    "total_instances": len(instances),
                    "instances": instances,
                },
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[AWSConnector] get_instance_status failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        """
        Generic fetch_data router cho LLM tool calling.
        params["action"] quyết định method nào được gọi.

        Supported actions:
          - "billing_summary" -> get_billing_summary()
          - "instance_status" -> get_instance_status()
        """
        action = params.get("action", "billing_summary")

        if action == "billing_summary":
            return await self.get_billing_summary(
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                granularity=params.get("granularity", "MONTHLY"),
                group_by=params.get("group_by"),
            )
        elif action == "instance_status":
            return await self.get_instance_status(
                state_filter=params.get("state_filter"),
                tag_filters=params.get("tag_filters"),
            )
        else:
            return ConnectorResult(
                success=False,
                error=f"Unknown action '{action}'. Supported: billing_summary, instance_status",
                source=self.config.name,
            )


# ---------------------------------------------------------------------------
# Module-level singleton — import từ bất kỳ đâu:
#   from mateai.infrastructure.connectors.aws_connector import aws_connector
# ---------------------------------------------------------------------------
aws_connector = AWSConnector()