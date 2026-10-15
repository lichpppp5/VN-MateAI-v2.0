# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Xác thực hai lớp (TOTP): đúng chuẩn RFC 6238, mật khẩu đúng KHÔNG đủ vào hệ thống, token trung gian không dùng làm token truy cập,
chống phát lại / dò mã, mã khôi phục dùng một lần, vai trò bắt buộc MFA chỉ được vào màn cài MFA, quản trị viên gỡ MFA có audit.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from mateai.application.security import mfa
from mateai.config.loader import settings
from mateai.infrastructure.database.db_manager import db_manager
from mateai.interfaces.http import routers  # noqa: F401
from mateai.interfaces.http.server import app

PASSWORD = "Matkhau#2026"


# ── chuẩn RFC 6238 (SHA-1, 6 chữ số = 6 số cuối của bộ mẫu 8 số) ────────────

@pytest.mark.parametrize("t,expected", [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                                        (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")])
def test_totp_matches_rfc6238_vectors(t, expected):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                       # base32 của "12345678901234567890"
    assert mfa.totp(secret, at=t) == expected


def test_verify_window_replay_and_garbage():
    s = mfa.new_secret()
    t = 1_800_000_000
    code = mfa.totp(s, at=t)
    step = mfa.verify(s, code, at=t)
    assert step == t // 30
    assert mfa.verify(s, code, at=t + 25) == step                       # lệch ~1 bước vẫn nhận
    assert mfa.verify(s, code, at=t + 95) is None                       # quá xa
    assert mfa.verify(s, code, at=t, last_step=step) is None            # cùng bước đã dùng -> từ chối (chống phát lại)
    for bad in ("", "12345", "abcdef", "1234567", None):
        assert mfa.verify(s, bad, at=t) is None
    assert len(mfa.new_secret()) >= 32 and mfa.new_secret() != mfa.new_secret()


def test_recovery_codes_are_single_use_and_stored_hashed():
    codes = mfa.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    import json
    stored = json.dumps([mfa.hash_recovery(c) for c in codes])
    assert codes[0] not in stored and codes[0].replace("-", "") not in stored
    ok, left = mfa.consume_recovery(stored, codes[0].lower())          # không phân biệt hoa thường / dấu gạch
    assert ok and len(json.loads(left)) == 9
    assert mfa.consume_recovery(left, codes[0])[0] is False             # dùng lại -> không
    assert mfa.consume_recovery(stored, "SAISAI-SAISAI")[0] is False


def test_qr_encodes_the_otpauth_uri_and_degrades_without_segno(monkeypatch):
    import sys
    uri = mfa.otpauth_uri("ABCDEF234567", "an@congty.vn")
    assert mfa.qr_data_uri(uri).startswith("data:image/svg+xml")
    monkeypatch.setitem(sys.modules, "segno", None)                                                    # thiếu thư viện -> chỉ còn khoá nhập tay
    assert mfa.qr_data_uri(uri) is None


def test_otpauth_uri_has_everything_an_authenticator_app_needs():
    uri = mfa.otpauth_uri("ABCDEF234567", "an@congty.vn")
    assert uri.startswith("otpauth://totp/VN-MateAI:an%40congty.vn?secret=ABCDEF234567") and "issuer=VN-MateAI" in uri and "period=30" in uri


# ── luồng HTTP đầy đủ qua middleware thật ───────────────────────────────────

class Clock:
    """Đồng hồ giả của riêng module MFA: mỗi mã chỉ dùng được một lần cho mỗi bước 30 s, nên bài thử phải 'chờ' sang bước kế."""
    def __init__(self):
        self.t = 1_900_000_000.0

    def __call__(self):
        return self.t

    def advance(self, steps=1):
        self.t += 30 * steps

    def code(self, secret):
        return mfa.totp(secret, at=self.t)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(mfa, "_now", c)
    return c


@pytest.fixture
def client(clock):
    from mateai.interfaces.http.routers.auth import _store
    c = TestClient(app, raise_server_exceptions=False)
    c.clock = clock
    yield c
    _store().events_clear("login:ip:testclient")


def new_user(role="viewer"):
    name = f"mfa-{uuid.uuid4().hex[:8]}"
    db_manager.create_user({"username": name, "password": PASSWORD, "role": role, "full_name": "Người thử MFA"})
    return name


def login(c, name, password=PASSWORD):
    return c.post("/api/v1/login", json={"username": name, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def enroll(c, name, role_token=None):
    """Bật MFA cho `name`; trả (secret, mã khôi phục, token truy cập)."""
    tok = role_token or login(c, name).json()["access_token"]
    secret = c.post("/api/v1/auth/mfa/setup", headers=bearer(tok)).json()["secret"]
    res = c.post("/api/v1/auth/mfa/enable", headers=bearer(tok), json={"code": c.clock.code(secret)})
    assert res.status_code == 200, res.text
    c.clock.advance()                                                   # bước đã dùng khi bật; lần đăng nhập sau dùng bước kế
    return secret, res.json()["recovery_codes"], res.json()


def test_without_mfa_login_is_unchanged(client):
    name = new_user()
    body = login(client, name).json()
    assert body["status"] == "success" and body["access_token"]
    assert client.get("/api/v1/auth/me", headers=bearer(body["access_token"])).status_code == 200


def test_setup_needs_a_valid_first_code_and_shows_secrets_once(client):
    name = new_user()
    tok = login(client, name).json()["access_token"]
    st = client.get("/api/v1/auth/mfa/status", headers=bearer(tok)).json()
    assert st["enabled"] is False and st["pending_setup"] is False
    setup = client.post("/api/v1/auth/mfa/setup", headers=bearer(tok)).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and setup["secret"] in setup["otpauth_uri"]
    assert setup["qr_svg"].startswith("data:image/svg+xml")                                            # quét được thay vì gõ khoá
    wrong = client.post("/api/v1/auth/mfa/enable", headers=bearer(tok), json={"code": "000000"})
    assert wrong.status_code == 400
    assert client.get("/api/v1/auth/mfa/status", headers=bearer(tok)).json()["enabled"] is False        # chưa bật khi chưa chứng minh
    ok = client.post("/api/v1/auth/mfa/enable", headers=bearer(tok), json={"code": client.clock.code(setup["secret"])})
    assert ok.status_code == 200 and len(ok.json()["recovery_codes"]) == 10
    st = client.get("/api/v1/auth/mfa/status", headers=bearer(tok)).json()
    assert st["enabled"] is True and st["recovery_codes_left"] == 10 and "secret" not in str(st)
    assert client.post("/api/v1/auth/mfa/setup", headers=bearer(tok)).status_code == 409             # đã bật: không sinh lại ngầm


def test_password_alone_does_not_log_in_and_the_intermediate_token_is_useless(client):
    name = new_user()
    secret, _codes, _ = enroll(client, name)
    first = login(client, name).json()
    assert first["status"] == "mfa_required" and "access_token" not in first and first["mfa_token"]
    for path in ("/api/v1/auth/me", "/api/v1/users", "/api/v1/auth/mfa/status", "/api/v1/enterprise/hitl/pending"):
        assert client.get(path, headers=bearer(first["mfa_token"])).status_code == 401, path
    assert client.post("/api/v1/login/mfa", json={"mfa_token": "khong.phai.token-hop-le", "code": "123456"}).status_code == 401
    done = client.post("/api/v1/login/mfa", json={"mfa_token": first["mfa_token"], "code": client.clock.code(secret)})
    assert done.status_code == 200 and done.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers=bearer(done.json()["access_token"])).status_code == 200


def test_a_normal_access_token_cannot_be_used_as_an_mfa_token(client):
    name = new_user()
    secret, _c, _ = enroll(client, name)
    plain = client.post("/api/v1/login/mfa", json={"mfa_token": _issue(name, None), "code": client.clock.code(secret)})
    assert plain.status_code == 401


def _issue(name, scope):
    from datetime import timedelta
    from mateai.application.security.auth_manager import auth_manager
    data = {"sub": name, "role": "viewer"}
    if scope:
        data["scope"] = scope
    return auth_manager.create_access_token(data=data, expires_delta=timedelta(minutes=5))


def test_a_code_cannot_be_replayed_and_wrong_codes_lock_the_account(client, monkeypatch):
    from mateai.interfaces.http.routers import auth as auth_router
    name = new_user()
    secret, _c, _ = enroll(client, name)
    first = login(client, name).json()
    good = client.clock.code(secret)
    assert client.post("/api/v1/login/mfa", json={"mfa_token": first["mfa_token"], "code": good}).status_code == 200
    again = login(client, name).json()
    # cùng mã (cùng bước 30 s) dùng lần thứ hai -> bị coi là phát lại
    assert client.post("/api/v1/login/mfa", json={"mfa_token": again["mfa_token"], "code": good}).status_code == 401
    monkeypatch.setattr(auth_router, "LOGIN_MAX_FAILURES", 3)
    first = again
    codes = ["111111", "222222"]                                           # 1 lần sai ở trên + 2 lần này = 3 -> khoá
    for c in codes:
        assert client.post("/api/v1/login/mfa", json={"mfa_token": first["mfa_token"], "code": c}).status_code == 401
    locked = client.post("/api/v1/login/mfa", json={"mfa_token": first["mfa_token"], "code": "444444"})
    assert locked.status_code == 429 and "Retry-After" in locked.headers


def test_recovery_code_logs_in_once(client):
    name = new_user()
    _secret, codes, _ = enroll(client, name)
    t1 = login(client, name).json()["mfa_token"]
    assert client.post("/api/v1/login/mfa", json={"mfa_token": t1, "code": codes[0]}).status_code == 200
    t2 = login(client, name).json()["mfa_token"]
    assert client.post("/api/v1/login/mfa", json={"mfa_token": t2, "code": codes[0]}).status_code == 401
    assert client.post("/api/v1/login/mfa", json={"mfa_token": t2, "code": codes[1]}).status_code == 200


def test_disable_needs_password_and_a_code_and_admin_can_reset(client):
    name = new_user()
    secret, codes, full = enroll(client, name)
    sess = client.post("/api/v1/login/mfa", json={"mfa_token": login(client, name).json()["mfa_token"], "code": codes[0]}).json()["access_token"]
    client.clock.advance()
    assert client.post("/api/v1/auth/mfa/disable", headers=bearer(sess), json={"password": "sai-mat-khau", "code": codes[1]}).status_code == 401
    assert client.post("/api/v1/auth/mfa/disable", headers=bearer(sess), json={"password": PASSWORD, "code": "000000"}).status_code == 401
    assert client.get("/api/v1/auth/mfa/status", headers=bearer(sess)).json()["enabled"] is True
    assert client.post("/api/v1/auth/mfa/disable", headers=bearer(sess), json={"password": PASSWORD, "code": codes[1]}).status_code == 200
    assert login(client, name).json()["status"] == "success"
    # quản trị viên gỡ hộ
    other = new_user()
    enroll(client, other)
    viewer_tok = _issue(new_user(), None)
    assert client.post(f"/api/v1/users/{other}/mfa/reset", headers=bearer(viewer_tok)).status_code == 403
    admin = new_user("admin")
    admin_tok = login(client, admin).json()["access_token"]
    assert client.post(f"/api/v1/users/{other}/mfa/reset", headers=bearer(admin_tok)).status_code == 200
    assert login(client, other).json()["status"] == "success"
    assert client.post("/api/v1/users/khong-co/mfa/reset", headers=bearer(admin_tok)).status_code == 404


def test_roles_that_require_mfa_can_only_enrol_until_they_have_it(client, monkeypatch):
    monkeypatch.setattr(settings.security, "require_mfa_roles", ["admin"])
    name = new_user("admin")
    first = login(client, name).json()
    assert first["status"] == "mfa_setup_required" and "access_token" not in first and first["setup_token"]
    st = first["setup_token"]
    for path in ("/api/v1/auth/me", "/api/v1/users", "/api/v1/enterprise/hitl/pending"):
        assert client.get(path, headers=bearer(st)).status_code == 401, path
    assert client.get("/api/v1/auth/mfa/status", headers=bearer(st)).status_code == 200
    secret = client.post("/api/v1/auth/mfa/setup", headers=bearer(st)).json()["secret"]
    done = client.post("/api/v1/auth/mfa/enable", headers=bearer(st), json={"code": client.clock.code(secret)}).json()
    assert done["access_token"] and len(done["recovery_codes"]) == 10                     # chứng minh xong -> được phiên đầy đủ
    assert client.get("/api/v1/auth/me", headers=bearer(done["access_token"])).status_code == 200
    # vai trò bắt buộc không tự tắt được
    assert client.post("/api/v1/auth/mfa/disable", headers=bearer(done["access_token"]),
                       json={"password": PASSWORD, "code": done["recovery_codes"][0]}).status_code == 403
    viewer = new_user("viewer")
    assert login(client, viewer).json()["status"] == "success"                            # vai trò khác không bị buộc


def test_secret_is_stored_sealed_and_never_listed(client):
    name = new_user()
    secret, _c, _ = enroll(client, name)
    user = db_manager.get_user_by_username_or_id(name)
    raw = db_manager.user_security_get(user["id"])["mfa_secret"]
    from mateai.config import secret_box
    if secret_box.enabled():
        assert raw != secret and raw.startswith("enc:")
    admin_tok = login(client, new_user("admin")).json()["access_token"]
    listing = client.get("/api/v1/users", headers=bearer(admin_tok))
    assert listing.status_code == 200 and secret not in listing.text and "mfa_secret" not in listing.text and "mfa_recovery" not in listing.text
    assert any(u["username"] == name and u.get("mfa_enabled") for u in listing.json().get("users", listing.json()) if isinstance(u, dict))


def test_sso_accounts_do_not_enrol_local_mfa():
    name = new_user()
    user = db_manager.get_user_by_username_or_id(name)
    db_manager.user_security_set(user["id"], auth_source="sso")
    with pytest.raises(mfa.MfaError, match="SSO"):
        mfa.begin_setup(name)
