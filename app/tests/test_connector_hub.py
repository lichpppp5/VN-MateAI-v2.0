# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_connector_hub.py
===========================
Bộ nối khai báo (`GenericConnector`) chạy với MÁY CHỦ GIẢ mô phỏng giao thức hạ tầng doanh nghiệp (tests/fake_enterprise.py):
xác thực (bearer / basic / header / query / OAuth2 / đăng nhập kiểu vCenter, GLPI, Veeam), TLS tự ký, phân trang, truy vấn
có tham số, JSON-RPC, thao tác can thiệp, che khoá, kiểu nội dung lạ.

Khi gắn vào hạ tầng thật chỉ cần KHAI BÁO — các logic dưới đây không đổi. Đây là mô phỏng theo tài liệu giao thức, không
thay thế việc thử trên hệ thống thật.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_enterprise import FakeServer  # noqa: E402

from mateai.infrastructure.connectors import custom_registry as reg  # noqa: E402
from mateai.infrastructure.connectors.base_connector import ConnectorConfig  # noqa: E402
from mateai.infrastructure.connectors.generic_connector import GenericConnector, clear_token_cache  # noqa: E402


@pytest.fixture(scope="module")
def other():
    with FakeServer() as s:
        yield s


@pytest.fixture(scope="module")
def srv(other):
    with FakeServer(other_origin=other.url) as s:
        yield s


@pytest.fixture(scope="module")
def tls():
    with FakeServer(tls=True) as s:
        yield s


@pytest.fixture(autouse=True)
def _fresh():
    clear_token_cache()
    yield


def make(base_url: str, **kw):
    """Dựng nguồn qua ĐÚNG bước chuẩn hoá của sổ đăng ký (kiểm cả khai báo hợp lệ)."""
    payload = {"title": "Nguồn thử", "base_url": base_url, **kw}
    rec = reg._normalise_source("thu-nghiem", payload, None)
    cfg = ConnectorConfig(name="ds:thu-nghiem", timeout_seconds=5.0, retry_count=kw.get("_retries", 2), retry_backoff_seconds=0.01,
                          extra={})
    return GenericConnector(rec, cfg)


# ── 1. Xác thực tĩnh ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,auth", [
    ("/open/items", {}),
    ("/bearer/items", {"auth_type": "bearer", "auth_value": "GOOD"}),
    ("/basic/items", {"auth_type": "basic", "auth_value": "admin:pw"}),
    ("/hdr/items", {"auth_type": "header", "auth_value": "K1", "auth_header": "X-Api-Key"}),
    ("/query/items", {"auth_type": "query", "auth_value": "Q1", "auth_query": "api_key"}),
])
async def test_static_auth_modes(srv, path, auth):
    res = await make(srv.url, default_path=path, **auth).fetch_data({})
    assert res.success, res.error
    assert res.data["returned"] >= 2 and res.data["rows"][0]["id"] == 1


async def test_wrong_credentials_report_http_status_and_hide_secret(srv):
    res = await make(srv.url, default_path="/bearer/items", auth_type="bearer", auth_value="SAI-KHOA-12345").fetch_data({})
    assert not res.success and "401" in res.error and "SAI-KHOA-12345" not in res.error


async def test_secret_echoed_by_the_remote_server_is_masked(srv):
    res = await make(srv.url, default_path="/leak", auth_type="bearer", auth_value="SECRET-TOKEN-ABCDEF").fetch_data({})
    assert not res.success and "SECRET-TOKEN-ABCDEF" not in res.error and "400" in res.error
    res = await make(srv.url, default_path="/leak", auth_type="header", auth_value="HDRKEY-9876543", auth_header="X-Api-Key").fetch_data({})
    assert not res.success and "HDRKEY-9876543" not in res.error


# ── 2. OAuth2 client-credentials ────────────────────────────────────────────

OAUTH = {"auth_type": "oauth2_client", "auth_value": "cid:csecret", "default_path": "/oauth/items",
         "oauth2": {"token_url": "{base}/oauth/token", "scope": "read"}}


def _oauth(srv, **over):
    cfg = {**OAUTH, "oauth2": {**OAUTH["oauth2"], "token_url": OAUTH["oauth2"]["token_url"].format(base=srv.url)}, **over}
    return make(srv.url, **cfg)


async def test_oauth2_token_is_cached_and_refreshed_after_expiry(srv):
    srv.state.calls.clear()
    c = _oauth(srv)
    assert (await c.fetch_data({})).success and (await c.fetch_data({})).success
    assert len(srv.calls_to("/oauth/token")) == 1                         # token dùng lại, không xin mới mỗi lần
    form = srv.calls_to("/oauth/token")[0]["body"]
    assert "grant_type=client_credentials" in form and "scope=read" in form and "client_secret=csecret" in form
    await _expire(srv)
    assert (await c.fetch_data({})).success                               # 401 -> tự lấy token mới, thử lại MỘT lần
    assert len(srv.calls_to("/oauth/token")) == 2


async def test_oauth2_basic_client_auth_and_bad_secret(srv):
    assert (await _oauth(srv, oauth2={"token_url": f"{srv.url}/oauth/token", "client_auth": "basic"}).fetch_data({})).success
    bad = await _oauth(srv, auth_value="cid:sai-secret").fetch_data({})
    assert not bad.success and "OAuth2" in bad.error and "sai-secret" not in bad.error


async def _expire(srv):
    import httpx
    async with httpx.AsyncClient() as c:
        await c.post(f"{srv.url}/test/expire")


# ── 3. Đăng nhập lấy token (vCenter / GLPI / Veeam) ─────────────────────────

VCENTER = {"auth_type": "login", "auth_value": "administrator@vsphere.local:pw", "default_path": "/vc/api/vcenter/vm",
           "login": {"method": "POST", "path": "/vc/api/session", "headers": {"Authorization": "Basic {basic}"},
                     "token_path": "", "token_header": "vmware-api-session-id", "token_prefix": ""}}
GLPI = {"auth_type": "login", "auth_value": "UT123", "default_path": "/glpi/apirest.php/Ticket",
        "login": {"method": "GET", "path": "/glpi/apirest.php/initSession", "headers": {"Authorization": "user_token {secret}"},
                  "token_path": "session_token", "token_header": "Session-Token", "token_prefix": ""}}
VEEAM = {"auth_type": "login", "auth_value": "veeam:pw", "default_path": "/veeam/api/v1/jobs",
         "login": {"method": "POST", "path": "/veeam/api/oauth2/token", "body_type": "form",
                   "body": {"grant_type": "password", "username": "{user}", "password": "{password}"},
                   "token_path": "access_token"}}


@pytest.mark.parametrize("cfg", [VCENTER, GLPI, VEEAM], ids=["vcenter", "glpi", "veeam"])
async def test_login_flows_fetch_and_reuse_session(srv, cfg):
    before = srv.state.logins
    c = make(srv.url, **cfg)
    r1, r2 = await c.fetch_data({}), await c.fetch_data({})
    assert r1.success and r2.success, (r1.error, r2.error)
    assert r1.data["returned"] >= 2
    assert srv.state.logins == before + 1                                  # đăng nhập MỘT lần, dùng lại phiên
    await _expire(srv)
    assert (await c.fetch_data({})).success                                # phiên hết hạn -> đăng nhập lại tự động
    assert srv.state.logins == before + 2


async def test_login_failure_is_explained_without_leaking_credentials(srv):
    res = await make(srv.url, **{**VCENTER, "auth_value": "administrator@vsphere.local:MAT-KHAU-SAI"}).fetch_data({})
    assert not res.success and "Đăng nhập thất bại" in res.error and "MAT-KHAU-SAI" not in res.error


async def test_login_without_token_at_path_says_what_came_back(srv):
    cfg = {**GLPI, "login": {**GLPI["login"], "token_path": "khong.co"}}
    res = await make(srv.url, **cfg).fetch_data({})
    assert not res.success and "token_path" in res.error and "session_token" in res.error


# ── 4. Phân trang ───────────────────────────────────────────────────────────

PAGING = {
    "page": ("/pg/items", {"type": "page", "page_size": 10}),
    "offset": ("/off/items", {"type": "offset", "page_size": 10}),
    "cursor": ("/cur/items", {"type": "cursor", "next_path": "meta.next_cursor"}),
    "next_url": ("/nu/items", {"type": "next_url", "next_path": "next"}),
    "link_header": ("/lh/items", {"type": "link_header"}),
}


@pytest.mark.parametrize("kind", list(PAGING))
async def test_pagination_collects_every_page(srv, kind):
    path, pag = PAGING[kind]
    res = await make(srv.url, default_path=path, pagination=pag, max_rows=100, row_limit=100).fetch_data({"limit": 100})
    assert res.success, res.error
    assert [r["id"] for r in res.data["rows"]] == list(range(1, 26))       # đủ 25 dòng, đúng thứ tự, không trùng
    assert res.data["truncated"] is False


@pytest.mark.parametrize("kind", list(PAGING))
async def test_pagination_stops_at_the_limit_and_says_so(srv, kind):
    path, pag = PAGING[kind]
    res = await make(srv.url, default_path=path, pagination=pag, max_rows=100, row_limit=12).fetch_data({"limit": 12})
    assert res.success and res.data["returned"] == 12 and res.data["truncated"] is True
    assert res.data["total"] > 12


async def test_without_pagination_only_the_first_page(srv):
    res = await make(srv.url, default_path="/pg/items", max_rows=100).fetch_data({"limit": 100})
    assert res.data["returned"] == 10 and res.data["total"] == 25 and res.data["truncated"] is True


async def test_next_page_on_another_host_is_never_followed(srv, other):
    other.state.calls.clear()
    res = await make(srv.url, default_path="/evil/items", auth_type="bearer", auth_value="GOOD",
                     pagination={"type": "next_url", "next_path": "next"}).fetch_data({})
    assert res.success and res.data["returned"] == 2 and res.data["truncated"] is True
    assert other.state.calls == []                                         # khoá không bị gửi sang máy chủ khác


async def test_server_that_ignores_the_page_parameter_does_not_loop(srv):
    srv.state.calls.clear()
    res = await make(srv.url, default_path="/same/items", max_rows=500, row_limit=500,
                     pagination={"type": "page", "page_size": 10, "max_pages": 20}).fetch_data({"limit": 500})
    assert res.data["returned"] == 10 and len(srv.calls_to("/same/items")) == 2


# ── 5. Truy vấn đặt tên có tham số / JSON-RPC ───────────────────────────────

PROM = {"queries": {"kiem tra": {"description": "Truy vấn PromQL", "method": "GET", "path": "/prom/api/v1/query",
                                 "query": {"query": "{expr}"}, "rows_path": "data.result",
                                 "params": {"expr": {"type": "string", "required": True, "max_length": 200}}}}}


async def test_named_query_with_parameters(srv):
    res = await make(srv.url, **PROM).fetch_data({"path": "kiem tra", "args": {"expr": "up == 0"}})
    assert res.success, res.error
    assert res.data["rows"][0]["value"][1] == "0" and res.data["rows"][0]["metric"]["instance"] == "srv-1:9100"
    assert srv.calls_to("/prom/api/v1/query")[-1]["query"]["query"] == "up == 0"


@pytest.mark.parametrize("args,why", [({}, "Thiếu tham số bắt buộc"), ({"expr": "x", "la": 1}, "không được khai báo"),
                                      ({"expr": "x" * 300}, "dài quá")])
async def test_named_query_rejects_bad_arguments(srv, args, why):
    res = await make(srv.url, **PROM).fetch_data({"path": "kiem tra", "args": args})
    assert not res.success and why in res.error


ZABBIX = {"auth_type": "bearer", "auth_value": "ZBXTOKEN", "queries": {"su co": {
    "method": "POST", "path": "/zabbix/api_jsonrpc.php", "rows_path": "result",
    "body": {"jsonrpc": "2.0", "method": "problem.get", "params": {"limit": "{n}", "output": "extend"}, "id": 1},
    "params": {"n": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}}}}}


async def test_json_rpc_post_query_keeps_typed_parameters(srv):
    res = await make(srv.url, **ZABBIX).fetch_data({"path": "su co", "args": {"n": "25"}})
    assert res.success and res.data["returned"] == 2 and res.data["rows"][1]["name"].startswith("Disk full")
    body = srv.calls_to("/zabbix/api_jsonrpc.php")[-1]["body"]
    assert '"limit":25' in body.replace(" ", "")                          # số, không phải chuỗi "25"
    bad = await make(srv.url, **ZABBIX).fetch_data({"path": "su co", "args": {"n": 1000}})
    assert not bad.success and "lớn hơn" in bad.error


async def test_json_rpc_error_message_reaches_the_operator(srv):
    res = await make(srv.url, **{**ZABBIX, "auth_value": "SAI"}).fetch_data({"path": "su co"})
    assert not res.success and "Invalid params" in res.error


async def test_path_parameters_cannot_escape_the_path(srv):
    q = {"vm": {"method": "GET", "path": "/api/vms/{vm_id}/info", "params": {"vm_id": {"type": "string", "required": True}}}}
    c = make(srv.url, queries=q)
    for evil in ("../../etc/passwd", "a/b", "..", "x?y=1", "a b"):
        res = await c.fetch_data({"path": "vm", "args": {"vm_id": evil}})
        assert not res.success and "không an toàn" in res.error, evil
    assert srv.calls_to("/api/vms/") == [] or all("/info" in c["path"] for c in srv.calls_to("/api/vms/"))


# ── 6. Thao tác can thiệp ───────────────────────────────────────────────────

ACTIONS = {"restart vm": {"description": "Khởi động lại máy ảo", "method": "POST", "path": "/api/vms/{vm_id}/restart",
                          "params": {"vm_id": {"type": "string", "required": True, "pattern": "^vm-[0-9]+$"}}},
           "xoa vm": {"method": "DELETE", "path": "/api/vms/{vm_id}", "risk_level": 5,
                      "params": {"vm_id": {"type": "string", "required": True}}},
           "tao phieu": {"method": "POST", "path": "/api/tickets",
                         "body": {"title": "{title}", "priority": "{prio}", "tags": ["auto"]},
                         "params": {"title": {"type": "string", "required": True}, "prio": {"type": "integer", "default": 3}}}}


async def test_actions_run_with_declared_parameters(srv):
    c = make(srv.url, auth_type="bearer", auth_value="GOOD", actions=ACTIONS)
    r = await c.run_action("restart vm", {"vm_id": "vm-7"})
    assert r.success and r.data["response"] == {"task": "T-vm-7", "state": "queued"}
    r = await c.run_action("tao phieu", {"title": "Máy chủ chậm"})
    assert r.success and r.data["response"]["echo"] == {"title": "Máy chủ chậm", "priority": 3, "tags": ["auto"]}
    assert (await c.run_action("xoa vm", {"vm_id": "vm-9"})).data["response"] == {"deleted": "vm-9"}


async def test_actions_reject_undeclared_names_and_bad_values(srv):
    c = make(srv.url, auth_type="bearer", auth_value="GOOD", actions=ACTIONS)
    assert "Không có thao tác" in (await c.run_action("phá hoại", {})).error
    assert "không đúng định dạng" in (await c.run_action("restart vm", {"vm_id": "evil"})).error
    assert "Thiếu tham số" in (await c.run_action("restart vm", {})).error


async def test_actions_are_never_retried_but_reads_are(srv):
    srv.state.flaky_hits = 0
    c = make(srv.url, actions={"ghi": {"method": "POST", "path": "/flaky-write"}}, queries={"doc": {"method": "GET", "path": "/flaky"}})
    res = await c.run_action("ghi", {})
    assert not res.success and srv.state.flaky_hits == 1                    # ghi hỏng: MỘT lần duy nhất
    srv.state.flaky_hits = 0
    res = await c.fetch_data({"path": "doc"})
    assert res.success and srv.state.flaky_hits == 3                        # đọc: thử lại tới khi được


async def test_write_actions_always_carry_a_risk_that_needs_approval():
    rec = reg._normalise_source("xx", {"title": "t", "base_url": "http://h", "actions": {"a": {"method": "POST", "path": "/a", "risk_level": 1}}}, None)
    assert rec["actions"]["a"]["risk_level"] == 3                           # không thể khai báo rủi ro thấp cho thao tác ghi


# ── 7. TLS ──────────────────────────────────────────────────────────────────

async def test_self_signed_https_default_fails_with_a_fix_hint(tls):
    res = await make(tls.url, default_path="/open/items").fetch_data({})
    assert not res.success and "ca_bundle" in res.error and "verify_ssl" in res.error


async def test_self_signed_https_with_verify_off_or_internal_ca(tls):
    assert (await make(tls.url, default_path="/open/items", verify_ssl=False).fetch_data({})).success
    assert (await make(tls.url, default_path="/open/items", ca_bundle=tls.ca_file).fetch_data({})).success
    miss = await make(tls.url, default_path="/open/items", ca_bundle="C:/khong/co/ca.pem").fetch_data({})
    assert not miss.success and "ca_bundle" in miss.error and "Không thấy tệp CA" in miss.error


# ── 8. Kiểu nội dung lạ / health ────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/odd/vnd", "/odd/plain"])
async def test_json_is_recognised_whatever_the_content_type(srv, path):
    res = await make(srv.url, default_path=path).fetch_data({})
    assert res.success and res.data["returned"] == 2


async def test_html_response_is_explained(srv):
    res = await make(srv.url, default_path="/odd/html").fetch_data({})
    assert not res.success and "không trả JSON" in res.error


async def test_health_check_needs_http_2xx_not_a_table(srv):
    assert (await make(srv.url, health_path="/healthz-text").health_check()).success          # thân "OK" dạng text
    assert (await make(srv.url, default_path="/health").health_check()).success
    assert (await make(srv.url, **VCENTER).health_check()).success                              # gồm cả đăng nhập
    bad = await make(srv.url, **{**VCENTER, "auth_value": "administrator@vsphere.local:sai"}).health_check()
    assert not bad.success and "Đăng nhập thất bại" in bad.error
    down = await make("http://127.0.0.1:9", _retries=0).health_check()
    assert not down.success


# ── 9. Khai báo hợp lệ / lưu trữ ────────────────────────────────────────────

@pytest.mark.parametrize("patch,why", [
    ({"queries": {"q": {"method": "DELETE", "path": "/x"}}}, "method chỉ nhận GET, POST"),
    ({"queries": {"q": {"method": "GET", "path": "/x/{ma}"}}}, "chưa được khai báo"),
    ({"queries": {"q": {"method": "GET", "path": "/x/../y"}}}, "'..'"),
    ({"actions": {"a": {"method": "GET", "path": "/x"}}}, "method chỉ nhận POST, PUT, PATCH, DELETE"),
    ({"queries": {"a": {"path": "/x"}}, "actions": {"a": {"method": "POST", "path": "/y"}}}, "trùng"),
    ({"extra_headers": {"Authorization": "Bearer x"}}, "không được phép"),
    ({"auth_type": "login"}, "cần khối `login`"),
    ({"auth_type": "login", "login": {"path": "/l", "body": "{bi_mat}"}}, "biến không có"),
    ({"auth_type": "oauth2_client"}, "cần khối `oauth2`"),
    ({"pagination": {"type": "cursor"}}, "cần `next_path`"),
    ({"pagination": {"type": "kỳ lạ"}}, "không hợp lệ"),
    ({"client_key": "k.pem"}, "client_cert"),
    ({"base_url": "http://169.254.169.254/latest"}, "metadata"),
])
def test_invalid_declarations_are_refused_with_a_clear_reason(patch, why):
    with pytest.raises(ValueError) as exc:
        reg._normalise_source("xx", {"title": "t", "base_url": "http://h", **patch}, None)
    assert why in str(exc.value)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "STORE_PATH", tmp_path / "data_sources.json")
    return reg.STORE_PATH


def test_secret_is_encrypted_on_disk_and_read_back_in_clear(store):
    reg.upsert_source("kho-1", {"title": "Kho", "base_url": "http://h", "auth_type": "bearer", "auth_value": "TOKEN-RAT-BI-MAT-1"})
    on_disk = store.read_text(encoding="utf-8")
    assert "TOKEN-RAT-BI-MAT-1" not in on_disk and "enc:v1:" in on_disk
    assert reg.get_source("kho-1")["auth_value"] == "TOKEN-RAT-BI-MAT-1"
    assert "auth_value" not in reg.get_source("kho-1", include_secrets=False) and reg.get_source("kho-1", include_secrets=False)["has_auth"]
    assert reg.list_sources(include_secrets=True)[0]["auth_value"] == "TOKEN-RAT-BI-MAT-1"
    reg.upsert_source("kho-1", {"title": "Kho 2", "base_url": "http://h", "auth_type": "bearer", "auth_value": ""})   # để trống = giữ khoá
    assert reg.get_source("kho-1")["auth_value"] == "TOKEN-RAT-BI-MAT-1"


def test_old_plaintext_files_are_migrated(store):
    import json
    store.write_text(json.dumps({"sources": {"cu-1": {"id": "cu-1", "title": "Cũ", "base_url": "http://h", "auth_type": "bearer",
                                                       "auth_value": "KHOA-DANG-RO-123"}}}), encoding="utf-8")
    assert reg.get_source("cu-1")["auth_value"] == "KHOA-DANG-RO-123" and reg.has_plaintext_secrets()
    assert reg.encrypt_existing() == 1 and not reg.has_plaintext_secrets()
    assert "KHOA-DANG-RO-123" not in store.read_text(encoding="utf-8") and reg.get_source("cu-1")["auth_value"] == "KHOA-DANG-RO-123"
    assert reg.encrypt_existing() == 0                                      # chạy lại không đổi gì
