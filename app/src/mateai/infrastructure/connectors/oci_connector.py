# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/connectors/oci_connector.py
================================
OCI (Oracle Cloud Infrastructure) Adapter — Phase 59 Universal Enterprise Connector Hub.

Sử dụng `oci-python-sdk`.
Cung cấp:
  - get_cloud_metrics(): Lấy tải CPU/RAM/Network của Compute instances (Monitoring API).
  - get_instance_list(): Danh sách Compute instances.

Yêu cầu cài đặt:
  pip install oci

Cấu hình (ENV VARS / config.json):
  OCI_CONFIG_FILE (mặc định: ~/.oci/config)
  OCI_PROFILE (mặc định: DEFAULT)
  OCI_COMPARTMENT_ID (OCID compartment gốc)
  OCI_REGION (mặc định: ap-singapore-1)
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


class OCIConnector(BaseConnector):
    """
    Adapter kết nối Oracle Cloud Infrastructure (OCI).
    """

    def __init__(self, config: Optional[ConnectorConfig] = None):
        if config is None:
            config = ConnectorConfig.from_settings("oci", load_connector_settings("oci"))
        super().__init__(config)
        self._config_oci = None  # oci.config
        self._compute_client = None
        self._monitoring_client = None
        self._identity_client = None

    def _reset_cached_clients(self) -> None:
        self._config_oci = None
        self._compute_client = None
        self._monitoring_client = None
        self._identity_client = None

    # ------------------------------------------------------------------
    # Authentication & Client Initialization
    # ------------------------------------------------------------------

    async def authenticate(self) -> bool:
        """Load OCI config file và khởi tạo service clients."""
        # `ConfigFileNotFound` / `InvalidConfig` chỉ tồn tại sau khi `import oci`
        # thành công. Nếu import lỗi (thiếu `oci-python-sdk`), khai báo chúng
        # ngoài try sẽ ném UnboundLocalError — che mất nguyên nhân thật là
        # "thiếu thư viện". Vì vậy phần import tách riêng và báo lỗi tường minh.
        try:
            import oci
            from oci.exceptions import ConfigFileNotFound, InvalidConfig
        except ImportError as e:
            logger.error(
                "[OCIConnector] Thiếu thư viện 'oci-python-sdk': %s — "
                "cài bằng `pip install oci`",
                e,
            )
            self._authenticated = False
            return False

        config_file = self.config.extra.get("config_file")
        profile = self.config.extra.get("profile", "DEFAULT")

        try:
            # Load config from file
            self._config_oci = oci.config.from_file(config_file, profile)

            # Override region if provided
            region = self.config.extra.get("region")
            if region:
                self._config_oci["region"] = region

            # Verify by listing compartments (lightweight call)
            self._identity_client = oci.identity.IdentityClient(self._config_oci)
            compartments = self._identity_client.list_compartments(
                compartment_id=self._config_oci["tenancy"],
                compartment_id_in_subtree=True,
            ).data
            logger.info("[OCIConnector] Authenticated. Tenancy: %s, Compartments: %d", self._config_oci["tenancy"], len(compartments))

            # Init service clients
            self._compute_client = oci.core.ComputeClient(self._config_oci)
            self._monitoring_client = oci.monitoring.MonitoringClient(self._config_oci)

            self._authenticated = True
            self._last_auth_time = __import__("time").time()
            self._token_expires_at = self._last_auth_time + 3600
            return True

        except ConfigFileNotFound:
            logger.error("[OCIConnector] OCI config file not found at %s. Run 'oci setup config'.", config_file)
            self._authenticated = False
            return False
        except InvalidConfig as e:
            logger.error("[OCIConnector] Invalid OCI config: %s", e)
            self._authenticated = False
            return False
        except Exception as e:
            logger.error("[OCIConnector] Authentication failed: %s", e, exc_info=True)
            self._authenticated = False
            return False

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    async def health_check(self) -> ConnectorResult:
        """Ping Identity service để verify OCI reachable."""
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Authentication failed", source=self.config.name)
        try:
            t0 = __import__("time").time()
            self._identity_client.get_tenancy(self._config_oci["tenancy"])
            latency = (__import__("time").time() - t0) * 1000
            return ConnectorResult(
                success=True,
                data={"status": "healthy", "service": "OCI Identity", "region": self._config_oci.get("region")},
                latency_ms=latency,
                source=self.config.name,
            )
        except Exception as e:
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    # ------------------------------------------------------------------
    # Business Methods
    # ------------------------------------------------------------------

    async def get_instance_list(
        self,
        compartment_id: Optional[str] = None,
        state_filter: Optional[List[str]] = None,
    ) -> ConnectorResult:
        """
        Lấy danh sách Compute instances.

        Args:
            compartment_id: OCID compartment. Mặc định: config OCI_COMPARTMENT_ID.
            state_filter: List lifecycle state (RUNNING, STOPPED, TERMINATED...). Mặc định: ["RUNNING"].

        Returns:
            ConnectorResult với data = {
                "total_instances": int,
                "instances": [
                    {"id": "ocid1.instance...", "display_name": "...", "state": "RUNNING",
                     "shape": "VM.Standard.E4.Flex", "availability_domain": "...",
                     "private_ip": "...", "public_ip": "...", "time_created": "..."},
                    ...
                ]
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        compartment_id = compartment_id or self.config.extra.get("compartment_id")
        if not compartment_id:
            return ConnectorResult(success=False, error="compartment_id is required (set OCI_COMPARTMENT_ID)", source=self.config.name)

        state_filter = state_filter or ["RUNNING"]

        try:
            t0 = __import__("time").time()

            instances = []
            list_instances_response = self._compute_client.list_instances(
                compartment_id=compartment_id,
                lifecycle_state=state_filter[0] if len(state_filter) == 1 else None,
            )

            for instance in list_instances_response.data:
                if state_filter and instance.lifecycle_state not in state_filter:
                    continue

                # Get VNIC info for IP addresses
                vnic_attachments = self._compute_client.list_vnic_attachments(
                    compartment_id=compartment_id,
                    instance_id=instance.id,
                ).data

                private_ips = []
                public_ips = []
                for attachment in vnic_attachments:
                    vnic = self._compute_client.get_vnic(attachment.vnic_id).data
                    if vnic.private_ip:
                        private_ips.append(vnic.private_ip)
                    if vnic.public_ip:
                        public_ips.append(vnic.public_ip)

                instances.append({
                    "id": instance.id,
                    "display_name": instance.display_name,
                    "state": instance.lifecycle_state,
                    "shape": instance.shape,
                    "availability_domain": instance.availability_domain,
                    "private_ips": private_ips,
                    "public_ips": public_ips,
                    "time_created": instance.time_created.isoformat() if instance.time_created else None,
                    "fault_domain": instance.fault_domain,
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
            logger.error("[OCIConnector] get_instance_list failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def get_cloud_metrics(
        self,
        compartment_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
        metrics: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        interval_minutes: int = 5,
    ) -> ConnectorResult:
        """
        Lấy metrics (CPU, Memory, Network) từ OCI Monitoring.

        Args:
            compartment_id: OCID compartment. Mặc định: config.
            instance_ids: List OCID instance để lọc. None = tất cả.
            metrics: List metric names. Mặc định: ["CpuUtilization", "MemoryUtilization", "NetworkBytesIn", "NetworkBytesOut"].
            start_time: ISO datetime. Mặc định: 1 giờ trước.
            end_time: ISO datetime. Mặc định: now.
            interval_minutes: Khoảng lấy mẫu (phút).

        Returns:
            ConnectorResult với data = {
                "metrics": {
                    "ocid1.instance...": {
                        "CpuUtilization": [{"timestamp": "...", "value": 45.2}, ...],
                        "MemoryUtilization": [...],
                    },
                    ...
                },
                "period": {"start": "...", "end": "..."}
            }
        """
        if not await self._ensure_authenticated():
            return ConnectorResult(success=False, error="Not authenticated", source=self.config.name)

        compartment_id = compartment_id or self.config.extra.get("compartment_id")
        if not compartment_id:
            return ConnectorResult(success=False, error="compartment_id is required", source=self.config.name)

        metrics = metrics or ["CpuUtilization", "MemoryUtilization", "NetworkBytesIn", "NetworkBytesOut"]

        # Default time range: last 1 hour
        if not end_time:
            end_time = datetime.utcnow().isoformat() + "Z"
        if not start_time:
            start_time = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"

        try:
            import oci
            t0 = __import__("time").time()

            # Build query
            metric_namespace = "oci_computeagent"
            query_parts = []
            for metric in metrics:
                query_parts.append(f'{metric}[{interval_minutes}m]{{}}')
            query = " ".join(query_parts)

            # Build filter for instance_ids if provided
            dimension_filters = []
            if instance_ids:
                for iid in instance_ids:
                    dimension_filters.append(f'resourceId = "{iid}"')
            else:
                # No instance filter - get all in compartment
                dimension_filters.append('compartmentId = "' + compartment_id + '"')

            summarize_metrics_response = self._monitoring_client.summarize_metrics_data(
                compartment_id=compartment_id,
                summarize_metrics_data_details=oci.monitoring.models.SummarizeMetricsDataDetails(
                    namespace=metric_namespace,
                    query=query,
                    start_time=start_time,
                    end_time=end_time,
                    resolution=f"{interval_minutes}m",
                ),
            )

            # Parse response
            metrics_by_instance = {}
            for metric_data in summarize_metrics_response.data:
                resource_id = metric_data.dimensions.get("resourceId", "unknown")
                metric_name = metric_data.name
                if resource_id not in metrics_by_instance:
                    metrics_by_instance[resource_id] = {}
                if metric_name not in metrics_by_instance[resource_id]:
                    metrics_by_instance[resource_id][metric_name] = []

                for point in metric_data.aggregated_datapoints:
                    metrics_by_instance[resource_id][metric_name].append({
                        "timestamp": point.timestamp.isoformat() if point.timestamp else None,
                        "value": point.value,
                        "unit": metric_data.metadata.get("unit", ""),
                    })

            latency = (__import__("time").time() - t0) * 1000
            return ConnectorResult(
                success=True,
                data={
                    "metrics": metrics_by_instance,
                    "period": {"start": start_time, "end": end_time},
                    "interval_minutes": interval_minutes,
                },
                latency_ms=latency,
                source=self.config.name,
            )

        except Exception as e:
            logger.error("[OCIConnector] get_cloud_metrics failed: %s", e, exc_info=True)
            return ConnectorResult(success=False, error=str(e), source=self.config.name)

    async def fetch_data(self, params: Dict[str, Any]) -> ConnectorResult:
        """
        Generic fetch_data router cho LLM tool calling.

        Supported actions:
          - "instance_list" -> get_instance_list()
          - "cloud_metrics" -> get_cloud_metrics()
        """
        action = params.get("action", "instance_list")

        if action == "instance_list":
            return await self.get_instance_list(
                compartment_id=params.get("compartment_id"),
                state_filter=params.get("state_filter"),
            )
        elif action == "cloud_metrics":
            return await self.get_cloud_metrics(
                compartment_id=params.get("compartment_id"),
                instance_ids=params.get("instance_ids"),
                metrics=params.get("metrics"),
                start_time=params.get("start_time"),
                end_time=params.get("end_time"),
                interval_minutes=params.get("interval_minutes", 5),
            )
        else:
            return ConnectorResult(
                success=False,
                error=f"Unknown action '{action}'. Supported: instance_list, cloud_metrics",
                source=self.config.name,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
oci_connector = OCIConnector()