# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/sso.py
==================================
Đăng nhập một lần (SSO) bằng OpenID Connect — Azure AD / Entra ID, Keycloak, Okta, Google Workspace… (docs/integrations/sso.md).
Luồng *authorization code + PKCE (S256)*; VN-MateAI là "relying party", mật khẩu chỉ nằm ở IdP của doanh nghiệp.

Kiểm tra bắt buộc với id_token (từ chối nếu thiếu / sai bất kỳ điều nào):
  chữ ký BẤT ĐỐI XỨNG (RS*/PS*/ES*, khoá lấy từ JWKS của issuer — không bao giờ chấp nhận `none` hay HS* ký bằng client_secret),
  `iss` = issuer khai báo, `aud` chứa client_id, `exp` còn hạn, `nonce` đúng của phiên, `sub` có mặt, email đã xác minh nếu IdP báo.
`state` dùng MỘT lần, hết hạn sau 10 phút. Mã đổi phiên (từ callback sang trình duyệt) dùng một lần, sống 60 giây — token không
bao giờ nằm trên URL.

Tài khoản: tự cấp khi `auto_provision`; vai trò suy từ nhóm / role của IdP qua `role_map`, KHÔNG BAO GIỜ tự cấp admin nếu không có
ánh xạ rõ; tài khoản cục bộ (có mật khẩu) trùng tên KHÔNG bị chiếm / liên kết ngầm (chống chiếm tài khoản qua email giả).
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

logger = logging.getLogger(__name__)

STATE_TTL_S = 600
CODE_TTL_S = 60
MAX_PENDING = 500
ASYMMETRIC_ALGS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
ROLE_ORDER = ("viewer", "manager", "admin")

_lock = threading.Lock()
_pending: Dict[str, Dict[str, Any]] = {}          # state -> {nonce, verifier, at}
_codes: Dict[str, Dict[str, Any]] = {}            # mã đổi phiên -> {user, at}
_disco: Dict[str, Any] = {"at": 0.0, "data": None, "issuer": ""}
_jwks: Dict[str, Any] = {"at": 0.0, "keys": {}, "uri": ""}


class SsoError(Exception):
    """Lỗi đăng nhập SSO — `args[0]` là câu nói được với người dùng (không lộ chi tiết nội bộ)."""


def cfg():
    from mateai.config.loader import settings
    return settings.sso


def public_config() -> Dict[str, Any]:
    c = cfg()
    ready = bool(c.enabled and c.issuer and c.client_id and c.redirect_uri)
    return {"enabled": ready, "display_name": c.display_name}


def reset() -> None:
    with _lock:
        _pending.clear()
        _codes.clear()
    _disco.update(at=0.0, data=None, issuer="")
    _jwks.update(at=0.0, keys={}, uri="")


def _private_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".local", ".lan", ".internal")) or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _check_url(url: str, what: str) -> str:
    p = urlsplit(url)
    if p.scheme == "https" and p.hostname:
        return url
    if p.scheme == "http" and p.hostname and _private_host(p.hostname):
        return url
    raise SsoError(f"{what} không hợp lệ (cần https)")


def _http_kwargs() -> Dict[str, Any]:
    c = cfg()
    return {"verify": (c.ca_bundle or bool(c.verify_ssl)), "follow_redirects": False}


async def _get_json(url: str) -> Any:
    import httpx
    async with httpx.AsyncClient(timeout=10.0, **_http_kwargs()) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def discovery() -> Dict[str, Any]:
    c = cfg()
    issuer = c.issuer.rstrip("/")
    if _disco["data"] and _disco["issuer"] == issuer and time.time() - _disco["at"] < 3600:
        return _disco["data"]
    try:
        data = await _get_json(_check_url(issuer, "issuer") + "/.well-known/openid-configuration")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SSO] không đọc được discovery: %s", exc)
        raise SsoError("Không kết nối được hệ thống đăng nhập của doanh nghiệp (IdP)")
    if str(data.get("issuer", "")).rstrip("/") != issuer:
        raise SsoError("IdP trả issuer không khớp cấu hình — từ chối")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        _check_url(str(data.get(key) or ""), key)
    _disco.update(at=time.time(), data=data, issuer=issuer)
    return data


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


async def start() -> str:
    """Tạo URL chuyển người dùng sang IdP (state + nonce + PKCE)."""
    c = cfg()
    if not public_config()["enabled"]:
        raise SsoError("SSO chưa được bật")
    d = await discovery()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    now = time.time()
    with _lock:
        for k in [k for k, v in _pending.items() if now - v["at"] > STATE_TTL_S]:
            _pending.pop(k, None)
        if len(_pending) >= MAX_PENDING:
            raise SsoError("Quá nhiều phiên đăng nhập đang chờ — thử lại sau ít phút")
        _pending[state] = {"nonce": nonce, "verifier": verifier, "at": now}
    q = {"response_type": "code", "client_id": c.client_id, "redirect_uri": c.redirect_uri, "scope": c.scopes, "state": state,
         "nonce": nonce, "code_challenge": _b64url(hashlib.sha256(verifier.encode("ascii")).digest()), "code_challenge_method": "S256"}
    return f"{d['authorization_endpoint']}{'&' if '?' in d['authorization_endpoint'] else '?'}{urlencode(q)}"


async def _signing_key(token: str) -> Tuple[Any, str]:
    import jwt
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")
    if alg not in ASYMMETRIC_ALGS:                              # chặn `none` và HS* (ký bằng client_secret rồi giả làm IdP)
        raise SsoError("id_token dùng thuật toán ký không được chấp nhận")
    kid = header.get("kid")
    d = await discovery()
    for attempt in (0, 1):                                      # không thấy khoá -> nạp lại JWKS MỘT lần (IdP vừa xoay khoá)
        if attempt == 1 or _jwks["uri"] != d["jwks_uri"] or time.time() - _jwks["at"] > 3600:
            data = await _get_json(d["jwks_uri"])
            _jwks.update(at=time.time(), uri=d["jwks_uri"], keys={(k.get("kid") or ""): k for k in data.get("keys", [])})
        jwk = _jwks["keys"].get(kid or "") or (next(iter(_jwks["keys"].values())) if not kid and len(_jwks["keys"]) == 1 else None)
        if jwk:
            return jwt.PyJWK(jwk).key, alg
    raise SsoError("Không tìm thấy khoá ký của IdP")


def _norm_groups(claims: Dict[str, Any]) -> List[str]:
    c = cfg()
    out: List[str] = []
    for name in (c.groups_claim, "roles"):
        v = claims.get(name)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v)
    return out


def map_role(claims: Dict[str, Any]) -> str:
    c = cfg()
    rmap = {str(k).strip().lower(): str(v).strip().lower() for k, v in (c.role_map or {}).items()}
    best = c.default_role if c.default_role in ROLE_ORDER else "viewer"
    for g in _norm_groups(claims):
        r = rmap.get(g.strip().lower())
        if r in ROLE_ORDER and ROLE_ORDER.index(r) > ROLE_ORDER.index(best):
            best = r
    return best


async def finish(code: str, state: str) -> Dict[str, Any]:
    """Đổi `code` lấy id_token, kiểm tra, cấp / cập nhật tài khoản. Trả {username, role, full_name}."""
    import httpx
    import jwt
    c = cfg()
    with _lock:
        pend = _pending.pop(str(state or ""), None)           # state dùng MỘT lần
    if not pend or time.time() - pend["at"] > STATE_TTL_S:
        raise SsoError("Phiên đăng nhập SSO không hợp lệ hoặc đã hết hạn — hãy thử lại")
    if not code:
        raise SsoError("IdP không trả mã xác thực")
    d = await discovery()
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": c.redirect_uri, "client_id": c.client_id,
            "code_verifier": pend["verifier"]}
    auth = (c.client_id, c.client_secret) if c.client_secret else None
    try:
        async with httpx.AsyncClient(timeout=10.0, **_http_kwargs()) as client:
            r = await client.post(d["token_endpoint"], data=form, auth=auth, headers={"Accept": "application/json"})
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SSO] đổi mã lỗi: %s", exc)
        raise SsoError("Không đổi được mã đăng nhập với IdP")
    if r.status_code != 200 or not body.get("id_token"):
        raise SsoError("IdP từ chối mã đăng nhập")
    token = str(body["id_token"])
    try:
        key, alg = await _signing_key(token)
        claims = jwt.decode(token, key, algorithms=[alg], audience=c.client_id, issuer=c.issuer.rstrip("/"), leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except SsoError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SSO] id_token không hợp lệ: %s", exc)
        raise SsoError("id_token không hợp lệ (chữ ký / đối tượng / hạn dùng)")
    if not secrets.compare_digest(str(claims.get("nonce") or ""), pend["nonce"]):
        raise SsoError("id_token không thuộc phiên đăng nhập này")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != c.client_id:
        raise SsoError("id_token có nhiều đối tượng nhưng không phải cấp cho ứng dụng này")
    return provision(claims)


def provision(claims: Dict[str, Any]) -> Dict[str, Any]:
    from mateai.application.security.auth_manager import auth_manager
    from mateai.infrastructure.database.db_manager import db_manager
    c = cfg()
    if claims.get("email_verified") is False:
        raise SsoError("Email của tài khoản chưa được IdP xác minh")
    raw = str(claims.get(c.username_claim) or claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if len(raw) < 3 or len(raw) > 120 or any(ch.isspace() for ch in raw):
        raise SsoError("IdP không trả định danh (email / tên đăng nhập) hợp lệ")
    email = str(claims.get("email") or raw).lower()
    domains = [d.strip().lower().lstrip("@") for d in (c.allowed_email_domains or []) if d.strip()]
    if domains and email.rsplit("@", 1)[-1] not in domains:
        raise SsoError("Tên miền email không được phép đăng nhập hệ thống này")
    full_name = str(claims.get(c.name_claim) or claims.get("name") or raw)[:120]
    role = map_role(claims)
    existing = auth_manager.get_user(raw)
    if existing:
        if (existing.get("auth_source") or "local") != "sso":
            raise SsoError("Đã có tài khoản cục bộ trùng tên — nhờ quản trị viên liên kết, hệ thống không tự liên kết")
        if existing.get("role") != role or existing.get("full_name") != full_name:
            try:
                db_manager.update_user(existing["id"], {"role": role, "full_name": full_name})      # vai trò theo IdP mỗi lần đăng nhập
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SSO] không cập nhật được vai trò: %s", exc)
        return {"username": existing["username"], "role": role, "full_name": full_name, "created": False}
    if not c.auto_provision:
        raise SsoError("Tài khoản của bạn chưa được cấp trong hệ thống — liên hệ quản trị viên")
    created = auth_manager.create_user({"username": raw, "password": secrets.token_urlsafe(32), "role": role, "full_name": full_name})
    db_manager.user_security_set(created["id"], auth_source="sso")
    return {"username": created["username"], "role": role, "full_name": full_name, "created": True}


def issue_exchange_code(user: Dict[str, Any]) -> str:
    code = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for k in [k for k, v in _codes.items() if now - v["at"] > CODE_TTL_S]:
            _codes.pop(k, None)
        _codes[code] = {"user": user, "at": now}
    return code


def redeem_exchange_code(code: str) -> Optional[Dict[str, Any]]:
    with _lock:
        entry = _codes.pop(str(code or ""), None)
    if not entry or time.time() - entry["at"] > CODE_TTL_S:
        return None
    return entry["user"]
