# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
presets.py — Mẫu khai báo cho hạ tầng doanh nghiệp phổ biến
===========================================================
Mỗi mẫu là một khai báo hoàn chỉnh cho `GenericConnector`; người vận hành chỉ điền địa chỉ máy chủ + khoá rồi lưu.

TRUNG THỰC VỀ ĐỘ TIN CẬY: các mẫu viết theo tài liệu API công khai của hãng và đã được kiểm thử luồng giao thức
bằng máy chủ giả (tests/fake_enterprise.py) — CHƯA thử trên hệ thống thật của hãng. Vì vậy mọi mẫu mang `verified=False`;
sau khi "Thử kết nối" thành công trên hạ tầng thật, người vận hành mới nên tin dùng. Khác biệt phiên bản
(đường dẫn, tên trường) sửa ngay trong khai báo, không cần sửa mã.

Thao tác can thiệp (`actions`) luôn bắt buộc duyệt (rủi ro >= 3) và chỉ admin dùng được qua AI.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

_STR = {"type": "string", "required": True}

PRESETS: List[Dict[str, Any]] = [
    {
        "key": "prometheus", "label": "Prometheus", "group": "Giám sát",
        "note": "Chỉ đọc. Nếu Prometheus đặt sau proxy xác thực, đổi kiểu xác thực tương ứng.",
        "base_url_hint": "http://prometheus.congty.local:9090",
        "declaration": {
            "title": "Prometheus", "category": "connector", "auth_type": "none", "monitor_type": "prometheus",
            "default_path": "/api/v1/alerts", "rows_path": "data.alerts", "health_path": "/-/healthy",
            "queries": {
                "canh bao": {"description": "Cảnh báo đang bắn", "method": "GET", "path": "/api/v1/alerts", "rows_path": "data.alerts"},
                "truy van": {"description": "Truy vấn PromQL tức thời", "method": "GET", "path": "/api/v1/query",
                             "query": {"query": "{promql}"}, "rows_path": "data.result",
                             "params": {"promql": {"type": "string", "required": True, "description": "Biểu thức PromQL, ví dụ up == 0"}}},
                "muc tieu": {"description": "Các target đang scrape", "method": "GET", "path": "/api/v1/targets", "rows_path": "data.activeTargets"},
            },
        },
    },
    {
        "key": "grafana", "label": "Grafana", "group": "Giám sát",
        "note": "Tạo Service Account token (vai trò Viewer) trong Grafana rồi dán vào ô Khoá. Nguồn này cũng được dùng cho màn Giám sát hạ tầng.",
        "base_url_hint": "https://grafana.congty.local",
        "declaration": {
            "title": "Grafana", "category": "connector", "auth_type": "bearer", "default_path": "/api/search",
            "health_path": "/api/health", "monitor_type": "grafana",
            "queries": {
                "dashboard": {"description": "Tìm dashboard", "method": "GET", "path": "/api/search",
                              "query": {"query": "{tu_khoa}", "type": "dash-db"},
                              "params": {"tu_khoa": {"type": "string", "default": ""}}},
                "canh bao": {"description": "Quy tắc cảnh báo (cấu hình)", "method": "GET", "path": "/api/v1/provisioning/alert-rules"},
                "canh bao dang bat": {"description": "Cảnh báo Grafana đang bật (Alertmanager nội bộ)", "method": "GET",
                                      "path": "/api/alertmanager/grafana/api/v2/alerts", "query": {"active": "true", "silenced": "false"}},
                "quy tac": {"description": "Trạng thái các quy tắc cảnh báo (firing / pending / normal)", "method": "GET",
                            "path": "/api/prometheus/grafana/api/v1/rules"},
            },
        },
    },
    {
        "key": "zabbix", "label": "Zabbix (JSON-RPC)", "group": "Giám sát",
        "note": "Zabbix 6.4+ nhận Bearer API token. Bản cũ hơn dùng user.login (đổi sang auth_type=login).",
        "base_url_hint": "https://zabbix.congty.local",
        "declaration": {
            "title": "Zabbix", "category": "connector", "auth_type": "bearer", "default_path": "su co",
            "queries": {
                "su co": {"description": "Sự cố gần đây", "method": "POST", "path": "/api_jsonrpc.php", "rows_path": "result",
                          "body": {"jsonrpc": "2.0", "method": "problem.get",
                                   "params": {"output": "extend", "recent": True, "sortfield": "eventid", "sortorder": "DESC", "limit": "{n}"}, "id": 1},
                          "params": {"n": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500}}},
                "may chu": {"description": "Danh sách máy chủ theo dõi", "method": "POST", "path": "/api_jsonrpc.php", "rows_path": "result",
                            "body": {"jsonrpc": "2.0", "method": "host.get", "params": {"output": ["hostid", "host", "status"], "limit": "{n}"}, "id": 1},
                            "params": {"n": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500}}},
            },
        },
    },
    {
        "key": "glpi", "label": "GLPI (ITSM / tài sản)", "group": "ITSM",
        "note": "Khoá = User API token của tài khoản GLPI. Bật REST API trong Cấu hình > Chung > API.",
        "base_url_hint": "https://glpi.congty.local/apirest.php",
        "declaration": {
            "title": "GLPI", "category": "connector", "auth_type": "login", "default_path": "/Ticket",
            "login": {"method": "GET", "path": "/initSession", "headers": {"Authorization": "user_token {secret}"},
                      "token_path": "session_token", "token_header": "Session-Token", "token_prefix": ""},
            "queries": {
                "ticket": {"description": "Ticket", "method": "GET", "path": "/Ticket", "query": {"sort": "19", "order": "DESC"}},
                "may tinh": {"description": "Máy tính trong kho tài sản", "method": "GET", "path": "/Computer"},
            },
            "actions": {
                "tao ticket": {"description": "Tạo ticket mới", "method": "POST", "path": "/Ticket",
                               "body": {"input": {"name": "{tieu_de}", "content": "{noi_dung}"}},
                               "params": {"tieu_de": _STR, "noi_dung": {"type": "string", "default": ""}}},
            },
        },
    },
    {
        "key": "jira", "label": "Jira Cloud", "group": "ITSM",
        "note": "Khoá = email:API-token (Basic). Jira Data Center dùng Bearer với Personal Access Token.",
        "base_url_hint": "https://congty.atlassian.net/rest/api/3",
        "declaration": {
            "title": "Jira", "category": "connector", "auth_type": "basic", "default_path": "tim issue", "rows_path": "issues",
            "queries": {
                "tim issue": {"description": "Tìm issue bằng JQL", "method": "GET", "path": "/search/jql",
                              "query": {"jql": "{jql}", "maxResults": "{n}", "fields": "summary,status,assignee,priority"},
                              "rows_path": "issues",
                              "params": {"jql": {"type": "string", "default": "statusCategory != Done ORDER BY updated DESC"},
                                         "n": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100}}},
            },
            "actions": {
                "binh luan": {"description": "Thêm bình luận vào issue", "method": "POST", "path": "/issue/{key}/comment",
                              "body": {"body": {"type": "doc", "version": 1,
                                                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "{noi_dung}"}]}]}},
                              "params": {"key": {"type": "string", "required": True, "pattern": "^[A-Z][A-Z0-9]+-[0-9]+$"}, "noi_dung": _STR}},
            },
        },
    },
    {
        "key": "servicenow", "label": "ServiceNow", "group": "ITSM",
        "note": "Khoá = user:mật khẩu (Basic) của tài khoản tích hợp có vai trò itil.",
        "base_url_hint": "https://congty.service-now.com/api/now",
        "declaration": {
            "title": "ServiceNow", "category": "connector", "auth_type": "basic", "default_path": "incident", "rows_path": "result",
            "pagination": {"type": "offset", "offset_param": "sysparm_offset", "size_param": "sysparm_limit", "page_size": 100},
            "queries": {
                "incident": {"description": "Incident đang mở", "method": "GET", "path": "/table/incident", "rows_path": "result",
                             "query": {"sysparm_query": "active=true^ORDERBYDESCsys_updated_on"}},
            },
            "actions": {
                "tao incident": {"description": "Tạo incident", "method": "POST", "path": "/table/incident",
                                 "body": {"short_description": "{tieu_de}", "description": "{noi_dung}"},
                                 "params": {"tieu_de": _STR, "noi_dung": {"type": "string", "default": ""}}},
            },
        },
    },
    {
        "key": "proxmox", "label": "Proxmox VE", "group": "Ảo hoá",
        "note": "Khoá = chuỗi đầy đủ PVEAPIToken=user@pam!tokenid=UUID. Dùng token quyền PVEAuditor để chỉ đọc.",
        "base_url_hint": "https://pve.congty.local:8006/api2/json",
        "declaration": {
            "title": "Proxmox VE", "category": "connector", "auth_type": "header", "auth_header": "Authorization",
            "default_path": "tai nguyen", "rows_path": "data",
            "queries": {
                "tai nguyen": {"description": "VM / container / node / storage", "method": "GET", "path": "/cluster/resources", "rows_path": "data"},
                "node": {"description": "Các node", "method": "GET", "path": "/nodes", "rows_path": "data"},
            },
            "actions": {
                "khoi dong vm": {"description": "Khởi động VM (qemu)", "method": "POST", "path": "/nodes/{node}/qemu/{vmid}/status/start",
                                 "params": {"node": {"type": "string", "required": True, "pattern": "^[A-Za-z0-9-]+$"},
                                            "vmid": {"type": "integer", "required": True, "minimum": 100}}},
            },
        },
    },
    {
        "key": "vcenter", "label": "VMware vCenter", "group": "Ảo hoá",
        "note": "Khoá = user:mật khẩu (ví dụ readonly@vsphere.local:***). REST vSphere 7.0U2+/8.",
        "base_url_hint": "https://vcenter.congty.local",
        "declaration": {
            "title": "vCenter", "category": "connector", "auth_type": "login", "default_path": "vm",
            "login": {"method": "POST", "path": "/api/session", "headers": {"Authorization": "Basic {basic}"},
                      "token_path": "", "token_header": "vmware-api-session-id", "token_prefix": ""},
            "queries": {
                "vm": {"description": "Danh sách máy ảo", "method": "GET", "path": "/api/vcenter/vm"},
                "host": {"description": "Máy chủ ESXi", "method": "GET", "path": "/api/vcenter/host"},
                "datastore": {"description": "Datastore", "method": "GET", "path": "/api/vcenter/datastore"},
            },
        },
    },
    {
        "key": "veeam", "label": "Veeam Backup & Replication", "group": "Sao lưu",
        "note": "Khoá = user:mật khẩu. Cổng REST mặc định 9419; header x-api-version cần khớp phiên bản Veeam.",
        "base_url_hint": "https://veeam.congty.local:9419",
        "declaration": {
            "title": "Veeam B&R", "category": "connector", "auth_type": "login", "default_path": "job",
            "extra_headers": {"x-api-version": "1.1-rev0"}, "rows_path": "data",
            "login": {"method": "POST", "path": "/api/oauth2/token", "body_type": "form",
                      "body": {"grant_type": "password", "username": "{user}", "password": "{password}"}, "token_path": "access_token"},
            "queries": {
                "job": {"description": "Trạng thái job sao lưu", "method": "GET", "path": "/api/v1/jobs/states", "rows_path": "data"},
                "phien": {"description": "Phiên sao lưu gần đây", "method": "GET", "path": "/api/v1/sessions", "rows_path": "data"},
            },
        },
    },
]


def catalog() -> List[Dict[str, Any]]:
    """Bản sao danh mục để gửi cho UI (kèm cờ `verified`)."""
    out = []
    for p in PRESETS:
        item = copy.deepcopy(p)
        item["verified"] = False
        out.append(item)
    return out
