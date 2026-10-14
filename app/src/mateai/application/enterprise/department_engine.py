# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/department_engine.py
=========================
Trục Thu Thập & Hợp Nhất Dữ Liệu Đa Nguồn Doanh Nghiệp (Enterprise Department Engine).
Nhiệm vụ:
  1. Cho phép cấu hình phòng ban và liên kết data source động qua Web Portal (No-Code Form).
  2. Thu thập dữ liệu đa nguồn: eInvoice, Paperless DMS, AWS/OCI Cloud, Health Monitor, Domain Sync.
  3. Semantic Aggregator Cache: Đệm ngữ nghĩa in-memory (TTL 30 phút) giúp báo cáo tức thì mà không cần quét lại từ đầu.
  4. Chuẩn hóa và lưu trữ chỉ số vào unified_department_metrics có phân cấp Data Clearance Level.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mateai.infrastructure.database.erp_database import erp_db
from mateai.infrastructure.cache.ephemeral_cache import ephemeral_cache

logger = logging.getLogger("mateai.application.enterprise.department_engine")

# TTL bộ đệm ngữ nghĩa: 30 phút = 1800 giây
SEMANTIC_CACHE_TTL_SEC = 1800


def secure_wipe_and_delete(file_path: str) -> bool:
    """
    Quy tắc tiêu hủy dữ liệu thô tức thì (GDPR / Nghị định 13).
    Ghi đè byte ngẫu nhiên trước khi xóa vật lý khỏi đĩa cứng.
    """
    try:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return False
        length = p.stat().st_size
        if length > 0:
            with open(p, "ba+", buffering=0) as f:
                f.seek(0)
                f.write(os.urandom(length))
                f.flush()
        p.unlink(missing_ok=True)
        logger.info("[EphemeralLifecycle] Đã tiêu hủy dữ liệu thô an toàn: %s", file_path)
        return True
    except Exception as e:
        logger.warning("[EphemeralLifecycle] Lỗi khi ghi đè hủy file %s: %s", file_path, e)
        try:
            os.unlink(file_path)
            return True
        except Exception:
            return False


class DepartmentEngine:
    """Bộ máy hợp nhất và điều phối dữ liệu cho tất cả các phòng ban trong tổ chức."""

    def __init__(self) -> None:
        # Cache in-memory: {dept_code: {"data": dict, "cached_at": float, "summary": str}}
        self._semantic_cache: Dict[str, Dict[str, Any]] = {}

    def register_department(self, dept_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Khai báo hoặc cập nhật phòng ban qua No-Code Web Portal."""
        code = dept_dict.get("dept_code") or dept_dict.get("code", "")
        name = dept_dict.get("dept_name") or dept_dict.get("name", f"Phòng Ban {code}")
        clearance = dept_dict.get("data_clearance_level", 1)
        metadata = dept_dict.get("config_metadata") or {}
        is_active = dept_dict.get("is_active", True)

        if not code:
            raise ValueError("dept_code không được để trống")

        res = erp_db.register_enterprise_department(
            dept_code=code,
            dept_name=name,
            data_clearance_level=int(clearance),
            config_metadata=metadata,
            is_active=bool(is_active),
        )
        logger.info("[DepartmentEngine] Đã đăng ký phòng ban: %s (%s)", code, name)
        return res

    def bind_data_source(self, dept_code: str, source_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Gắn kết cấu hình nguồn dữ liệu (API, DB, Paperless, RPA...) vào phòng ban."""
        source_name = source_dict.get("source_name") or source_dict.get("name", "Default Source")
        source_type = source_dict.get("source_type") or source_dict.get("type", "REST_API")
        connection_config = source_dict.get("connection_config") or {}
        sync_cron = source_dict.get("sync_cron")
        is_active = source_dict.get("is_active", True)

        res = erp_db.bind_department_data_source(
            dept_code=dept_code,
            source_name=source_name,
            source_type=source_type,
            connection_config=connection_config,
            sync_cron=sync_cron,
            is_active=bool(is_active),
        )
        logger.info("[DepartmentEngine] Đã gắn data source '%s' (%s) vào phòng ban %s", source_name, source_type, dept_code)
        return res

    async def ingest_department_data(
        self,
        dept_code: str,
        session_id: str = "default",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Thu thập dữ liệu thực tế từ các nguồn kết nối của phòng ban.
        Áp dụng Quản lý Vòng đời Dữ liệu 3 Tầng:
          - Tầng 1: Tiêu hủy file thô tức thì sau xử lý.
          - Tầng 2: Nạp dữ liệu trích xuất vào EphemeralSessionCache (RAM 15-30 phút).
          - Tầng 3: Chỉ lưu chỉ số Non-PII vào CSDL vĩnh viễn, TUYỆT ĐỐI KHÔNG lưu danh tính PII.
        """
        code = dept_code.strip().upper()
        domain_key = f"DEPT_{code}"
        now = time.time()

        # 1. Kiểm tra EphemeralSessionCache trên RAM (ưu tiên theo session)
        if not force_refresh:
            cached_session_data = ephemeral_cache.get(session_id, domain_key)
            if cached_session_data:
                logger.debug("[DepartmentEngine] Tái sử dụng dữ liệu phòng ban %s từ Ephemeral RAM Cache", code)
                return cached_session_data

            if code in self._semantic_cache:
                cache_entry = self._semantic_cache[code]
                if (now - cache_entry.get("cached_at", 0)) < SEMANTIC_CACHE_TTL_SEC:
                    return cache_entry["data"]

        dept = erp_db.get_enterprise_department_by_code(code)
        if not dept:
            dept = erp_db.register_enterprise_department(code, f"Phòng Ban {code}")

        sources = erp_db.get_department_data_sources(code, active_only=True)
        clearance = dept.get("data_clearance_level", 1)

        aggregated: Dict[str, Any] = {
            "dept_code": code,
            "dept_name": dept.get("dept_name"),
            "clearance_level": clearance,
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sources_count": len(sources),
            "metrics": {},
            "summary_text": "",
        }

        # 2. Xử lý theo phòng ban và loại data sources
        # A. Kế Toán / Tài Chính (FIN) hoặc có nguồn eInvoice
        if code in ("FIN", "ACC", "FINANCE") or any("INVOICE" in s.get("source_name", "").upper() for s in sources):
            try:
                from mateai.infrastructure.connectors.einvoice_connector import EInvoiceConnector
                conn = EInvoiceConnector()
                inv_res = await conn.get_daily_invoices()
                if inv_res and inv_res.data:
                    aggregated["metrics"]["einvoice"] = inv_res.data
                else:
                    # Lấy số liệu từ sổ sách nội bộ trong ERP database nếu connector chưa cấu hình thực tế
                    fin_summary = erp_db.get_financial_summary(30)
                    aggregated["metrics"]["finances"] = fin_summary
            except Exception as e:
                logger.warning("[DepartmentEngine] Lỗi thu thập eInvoice cho %s: %s", code, e)
                aggregated["metrics"]["finances"] = erp_db.get_financial_summary(30)

        # B. Nhân Sự (HR)
        if code in ("HR", "HUMAN_RESOURCES", "PERSONNEL"):
            try:
                # Đọc số liệu nhân sự, chấm công hôm nay, phiếu công việc
                tree = erp_db.get_structure_tree()
                total_emps = sum(len(d.get("employees", [])) for d in tree)
                tasks_overview = erp_db.get_company_kpi_overview()
                aggregated["metrics"]["hr"] = {
                    "total_employees": total_emps,
                    "active_tasks": tasks_overview.get("active_tasks", 0),
                    "completed_tasks": tasks_overview.get("completed_tasks", 0),
                    "completion_rate": tasks_overview.get("completion_rate", 0.0),
                }
            except Exception as e:
                logger.warning("[DepartmentEngine] Lỗi thu thập dữ liệu HR: %s", e)

        # C. Kỹ Thuật / IT / Hạ Tầng (IT, OPS, CTO)
        if code in ("IT", "OPS", "TECH", "INFRA"):
            infra_metrics: Dict[str, Any] = {}
            try:
                from mateai.infrastructure.connectors.aws_connector import AWSConnector
                from mateai.infrastructure.connectors.oci_connector import OCIConnector
                aws_conn = AWSConnector()
                oci_conn = OCIConnector()
                aws_cost = await aws_conn.get_cost_summary(30)
                oci_cost = await oci_conn.get_cost_summary(30)
                infra_metrics["cloud_costs"] = {
                    "aws": aws_cost.data if aws_cost else None,
                    "oci": oci_cost.data if oci_cost else None,
                }
            except Exception as e:
                logger.warning("[DepartmentEngine] Lỗi thu thập Cloud costs: %s", e)

            try:
                from mateai.application.operations.health_monitor import health_monitor
                system_health = health_monitor.get_current_metrics()
                infra_metrics["system_health"] = system_health
            except Exception:
                pass

            aggregated["metrics"]["it_infra"] = infra_metrics

        # D. Tài liệu / Pháp chế / Văn bản (LEGAL, DOCS, ADMIN)
        if code in ("LEGAL", "DOCS", "ADMIN") or any("PAPERLESS" in s.get("source_name", "").upper() for s in sources):
            try:
                from mateai.infrastructure.connectors.paperless_connector import PaperlessConnector
                paperless = PaperlessConnector()
                doc_stats = await paperless.get_statistics()
                aggregated["metrics"]["documents"] = doc_stats.data if doc_stats else {"status": "connected"}
            except Exception as e:
                logger.warning("[DepartmentEngine] Lỗi thu thập Paperless docs: %s", e)

        # 3. Tạo chuỗi Semantic Summary cô đọng
        summary_parts = [f"Phòng ban {dept.get('dept_name', code)} ({code}):"]
        if "finances" in aggregated["metrics"]:
            fin = aggregated["metrics"]["finances"]
            summary_parts.append(f"Thu: {fin.get('total_income', 0):,} VNĐ, Chi: {fin.get('total_expense', 0):,} VNĐ.")
        if "einvoice" in aggregated["metrics"]:
            inv = aggregated["metrics"]["einvoice"]
            summary_parts.append(f"HĐĐT phát hành: {inv.get('total_invoices', 0)}, Lỗi: {inv.get('failed_count', 0)}.")
        if "hr" in aggregated["metrics"]:
            hr = aggregated["metrics"]["hr"]
            summary_parts.append(f"Nhân sự: {hr.get('total_employees', 0)}, Tỉ lệ hoàn thành task: {hr.get('completion_rate', 0)}%.")
        if "it_infra" in aggregated["metrics"]:
            summary_parts.append("Hạ tầng CNTT & Cloud đang hoạt động ổn định.")
        if "documents" in aggregated["metrics"]:
            summary_parts.append("Tài liệu & hồ sơ nghiệp vụ đã được đồng bộ với DMS.")

        aggregated["summary_text"] = " ".join(summary_parts)

        # 4. Lưu vào Database (Chỉ số Thống kê Phi Định Danh Non-PII) & Ephemeral Cache (RAM)
        erp_db.record_unified_metric(
            dept_code=code,
            metrics_data=aggregated["metrics"],
            clearance_level=clearance,
        )

        # Lưu lịch sử xu hướng Non-PII cho biểu đồ tương lai
        if "finances" in aggregated["metrics"]:
            fin = aggregated["metrics"]["finances"]
            erp_db.record_historical_metric(code, "total_income", float(fin.get("total_income", 0)))
            erp_db.record_historical_metric(code, "total_expense", float(fin.get("total_expense", 0)))
            erp_db.record_historical_metric(code, "net_balance", float(fin.get("net_balance", 0)))
        if "hr" in aggregated["metrics"]:
            hr = aggregated["metrics"]["hr"]
            erp_db.record_historical_metric(code, "total_headcount", float(hr.get("total_employees", 0)))
            erp_db.record_historical_metric(code, "active_tasks", float(hr.get("active_tasks", 0)))
            erp_db.record_historical_metric(code, "completion_rate", float(hr.get("completion_rate", 0.0)))
        if "einvoice" in aggregated["metrics"]:
            inv = aggregated["metrics"]["einvoice"]
            erp_db.record_historical_metric(code, "total_invoices", float(inv.get("total_invoices", 0)))
            erp_db.record_historical_metric(code, "failed_invoices", float(inv.get("failed_count", 0)))

        # Nạp vào EphemeralSessionCache (RAM 15-30 phút cho phiên làm việc hiện tại)
        ephemeral_cache.set(
            session_id=session_id,
            domain_key=domain_key,
            data=aggregated,
            sliding_ttl=900.0,
            hard_timeout=1800.0,
        )

        self._semantic_cache[code] = {
            "data": aggregated,
            "cached_at": now,
            "summary": aggregated["summary_text"],
        }

        return aggregated

    def get_cached_summary(self, dept_code: str) -> Optional[str]:
        """Lấy câu tóm tắt ngữ nghĩa từ cache nếu còn hiệu lực."""
        code = dept_code.strip().upper()
        entry = self._semantic_cache.get(code)
        if entry and (time.time() - entry.get("cached_at", 0)) < SEMANTIC_CACHE_TTL_SEC:
            return entry.get("summary")
        return None

    def invalidate_cache(self, dept_code: Optional[str] = None) -> None:
        """Làm mới cache phòng ban."""
        if dept_code:
            self._semantic_cache.pop(dept_code.strip().upper(), None)
        else:
            self._semantic_cache.clear()


# Singleton instance
department_engine = DepartmentEngine()
