# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
SSO OIDC (authorization code + PKCE) với IdP giả ký RSA thật (tests/fake_oidc.py): luồng đúng, vai trò từ nhóm, và các đòn tấn công
điển hình — id_token giả (alg none / HS256 / khoá lạ), sai issuer / audience / nonce / hạn, tái sử dụng state / mã, chiếm tài khoản
cục bộ, email chưa xác minh, tên miền lạ.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from fake_oidc import FakeOidc  # noqa: E402

from mateai.application.security import sso  # noqa: E402
from mateai.config.loader import settings  # noqa: E402
from mateai.infrastructure.database.db_manager import db_manager  # noqa: E402
from mateai.interfaces.http.server import app  # noqa: E402

REDIRECT = "https://vn.congty.local/api/v1/sso/callback"


@pytest.fixture(scope="module")
def idp():
    with FakeOidc() as srv:
        yield srv


@pytest.fixture
def cfg(idp, monkeypatch):
    values = dict(enabled=True, issuer=idp.issuer, client_id=idp.client_id, client_secret=idp.client_secret, redirect_uri=REDIRECT,
                  role_map={"VN-Admins": "admin", "VN-Managers": "manager"}, default_role="viewer", allowed_email_domains=[],
                  auto_provision=True, groups_claim="groups", username_claim="email")
    for k, v in values.items():
        monkeypatch.setattr(settings.sso, k, v)
    idp.issuer_override = None
    idp.token_calls = 0
    sso.reset()
    yield settings.sso
    sso.reset()


@pytest.fixture
def client(cfg):
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def unique_email():
    return f"nv{uuid.uuid4().hex[:8]}@congty.vn"


def begin(client):
    """Bấm 'Đăng nhập SSO': trả (state, nonce, challenge) lấy từ URL chuyển sang IdP."""
    r = client.get("/api/v1/sso/login")
    assert r.status_code == 302, r.text
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["response_type"] == ["code"] and q["client_id"] == ["vnmateai"]
    assert q["redirect_uri"] == [REDIRECT] and "openid" in q["scope"][0]
    return q["state"][0], q["nonce"][0], q["code_challenge"][0]


def run(client, idp, claims=None, *, state_override=None, **opts):
    state, nonce, challenge = begin(client)
    code = "code-" + uuid.uuid4().hex[:10]
    idp.register(code, challenge=challenge, nonce=opts.pop("nonce", nonce), redirect_uri=REDIRECT, claims=claims, **opts)
    return client.get("/api/v1/sso/callback", params={"code": code, "state": state_override or state})


def ok_code(resp):
    assert resp.status_code == 302 and "/#sso=" in resp.headers["location"], resp.headers.get("location")
    return resp.headers["location"].split("#sso=", 1)[1]


def failed_with(resp):
    assert resp.status_code == 302 and resp.headers["location"].startswith("/?sso_error="), resp.headers["location"]
    return unquote(resp.headers["location"].split("sso_error=", 1)[1])


# ── luồng đúng ──────────────────────────────────────────────────────────────

def test_config_is_public_and_hides_secrets(client):
    body = client.get("/api/v1/sso/config").json()
    assert body == {"enabled": True, "display_name": settings.sso.display_name}
    assert "S3CRET" not in str(body)


def test_disabled_sso_never_redirects(client, monkeypatch):
    monkeypatch.setattr(settings.sso, "enabled", False)
    assert client.get("/api/v1/sso/config").json()["enabled"] is False
    r = client.get("/api/v1/sso/login")
    assert r.status_code == 302 and r.headers["location"].startswith("/?sso_error=")


def test_happy_path_provisions_a_viewer_and_the_token_never_appears_in_a_url(client, idp):
    email = unique_email()
    resp = run(client, idp, {"email": email, "name": "Trần Bình"})
    code = ok_code(resp)
    assert "access_token" not in resp.headers["location"] and "eyJ" not in resp.headers["location"]
    ex = client.post("/api/v1/sso/exchange", json={"code": code})
    assert ex.status_code == 200
    body = ex.json()
    assert body["user"] == {"username": email, "full_name": "Trần Bình", "role": "viewer"}
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["user"]["username"] == email
    assert client.post("/api/v1/sso/exchange", json={"code": code}).status_code == 401              # mã dùng một lần
    assert db_manager.user_security_get(db_manager.get_user_by_username_or_id(email)["id"])["auth_source"] == "sso"


def test_exchange_code_expires(client, idp):
    code = ok_code(run(client, idp, {"email": unique_email()}))
    sso._codes[code]["at"] -= sso.CODE_TTL_S + 5
    assert client.post("/api/v1/sso/exchange", json={"code": code}).status_code == 401


def test_roles_come_from_idp_groups_and_never_default_to_admin(client, idp):
    admin, mgr, plain = unique_email(), unique_email(), unique_email()
    assert client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": admin, "groups": ["vn-admins", "Khac"]}))}).json()["user"]["role"] == "admin"
    assert client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": mgr, "groups": ["VN-Managers"]}))}).json()["user"]["role"] == "manager"
    assert client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": plain, "groups": ["Nhom-Khong-Ro", "admin"]}))}).json()["user"]["role"] == "viewer"
    both = unique_email()
    assert client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": both, "groups": ["VN-Managers", "VN-Admins"]}))}).json()["user"]["role"] == "admin"


def test_role_follows_the_idp_on_every_login(client, idp):
    email = unique_email()
    client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": email, "groups": ["VN-Admins"]}))})
    assert db_manager.get_user_by_username_or_id(email)["role"] == "admin"
    client.post("/api/v1/sso/exchange", json={"code": ok_code(run(client, idp, {"email": email, "groups": []}))})
    assert db_manager.get_user_by_username_or_id(email)["role"] == "viewer"                         # bị rút quyền ở IdP -> mất quyền ở đây


def test_sso_accounts_cannot_log_in_with_a_password(client, idp):
    email = unique_email()
    ok_code(run(client, idp, {"email": email}))
    for guess in ("password", "123456", "admin123", email):
        assert client.post("/api/v1/login", json={"username": email, "password": guess}).status_code == 401


# ── tấn công ────────────────────────────────────────────────────────────────

def test_state_is_single_use_and_unknown_state_is_refused(client, idp):
    state, nonce, challenge = begin(client)
    idp.register("c1", challenge=challenge, nonce=nonce, redirect_uri=REDIRECT, claims={"email": unique_email()})
    assert ok_code(client.get("/api/v1/sso/callback", params={"code": "c1", "state": state}))
    idp.register("c2", challenge=challenge, nonce=nonce, redirect_uri=REDIRECT, claims={"email": unique_email()})
    assert "hết hạn" in failed_with(client.get("/api/v1/sso/callback", params={"code": "c2", "state": state}))      # dùng lại state
    assert "hết hạn" in failed_with(client.get("/api/v1/sso/callback", params={"code": "c2", "state": "doan-mo"}))


def test_expired_state_is_refused(client, idp):
    state, nonce, challenge = begin(client)
    sso._pending[state]["at"] -= sso.STATE_TTL_S + 5
    idp.register("c3", challenge=challenge, nonce=nonce, redirect_uri=REDIRECT, claims={"email": unique_email()})
    assert "hết hạn" in failed_with(client.get("/api/v1/sso/callback", params={"code": "c3", "state": state}))


@pytest.mark.parametrize("why,kwargs,claims", [
    ("alg none", {"alg": "none"}, None),
    ("HS256 ký bằng client_secret", {"alg": "HS256"}, None),
    ("ký bằng khoá RSA lạ", {"sign_with": "other"}, None),
    ("sai audience", {}, {"aud": "ung-dung-khac"}),
    ("sai issuer", {}, {"iss": "https://ke-gia-mao.example"}),
    ("đã hết hạn", {}, {"exp": 1_000_000_000, "iat": 999_999_000}),
    ("sai nonce", {"nonce": "nonce-cua-ke-khac"}, None),
    ("thiếu sub", {"drop": ("sub",)}, None),
    ("thiếu exp", {"drop": ("exp",)}, None),
])
def test_forged_or_mismatched_id_tokens_are_refused(client, idp, why, kwargs, claims):
    email = unique_email()
    resp = run(client, idp, {"email": email, **(claims or {})}, **kwargs)
    assert failed_with(resp), why
    assert db_manager.get_user_by_username_or_id(email) is None, f"{why}: không được tạo tài khoản"


def test_unverified_email_and_foreign_domains_are_refused(client, idp, monkeypatch):
    e1 = unique_email()
    assert "xác minh" in failed_with(run(client, idp, {"email": e1, "email_verified": False}))
    assert db_manager.get_user_by_username_or_id(e1) is None
    monkeypatch.setattr(settings.sso, "allowed_email_domains", ["congty.vn"])
    assert "tên miền" in failed_with(run(client, idp, {"email": "ke-la@gmail.com"})).lower()
    assert ok_code(run(client, idp, {"email": unique_email()}))


def test_a_local_password_account_is_never_taken_over(client, idp):
    email = unique_email()
    db_manager.create_user({"username": email, "password": "Matkhau#2026", "role": "admin", "full_name": "Quản trị cục bộ"})
    msg = failed_with(run(client, idp, {"email": email, "groups": []}))
    assert "tài khoản cục bộ" in msg
    after = db_manager.get_user_by_username_or_id(email)
    assert after["role"] == "admin" and after["full_name"] == "Quản trị cục bộ"                      # không bị đổi vai trò / tên
    assert client.post("/api/v1/login", json={"username": email, "password": "Matkhau#2026"}).status_code == 200


def test_no_auto_provision_means_unknown_people_cannot_enter(client, idp, monkeypatch):
    monkeypatch.setattr(settings.sso, "auto_provision", False)
    email = unique_email()
    assert "chưa được cấp" in failed_with(run(client, idp, {"email": email}))
    assert db_manager.get_user_by_username_or_id(email) is None


def test_idp_errors_are_shown_without_crashing(client):
    r = client.get("/api/v1/sso/callback", params={"error": "access_denied", "error_description": "Người dùng từ chối"})
    assert "từ chối" in failed_with(r)
    assert failed_with(client.get("/api/v1/sso/callback")) != ""                                      # thiếu tham số


def test_idp_with_a_different_issuer_in_discovery_is_refused(client, idp):
    idp.issuer_override = "https://idp-khac.example"
    sso.reset()
    assert "issuer" in failed_with(client.get("/api/v1/sso/login")).lower()


def test_wrong_pkce_verifier_is_caught_by_the_idp(client, idp):
    state, nonce, challenge = begin(client)
    idp.register("c4", challenge="challenge-sai-hoan-toan", nonce=nonce, redirect_uri=REDIRECT, claims={"email": unique_email()})
    assert "từ chối" in failed_with(client.get("/api/v1/sso/callback", params={"code": "c4", "state": state}))


def test_public_endpoints_work_without_a_session_but_others_still_need_one(client):
    assert client.get("/api/v1/sso/config").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/sso/exchange", json={"code": "x" * 20}).status_code == 401
