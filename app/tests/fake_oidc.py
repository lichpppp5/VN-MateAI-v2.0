# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/fake_oidc.py
==================
IdP OpenID Connect GIẢ (khoá RSA thật, JWKS thật, kiểm PKCE S256 ở token endpoint) để kiểm thử luồng SSO mà không cần Azure AD /
Keycloak. Mô phỏng theo đặc tả OIDC Core + RFC 7636; KHÔNG thay thế việc thử với IdP thật của doanh nghiệp.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
import time
from typing import Any, Dict, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeOidc:
    def __init__(self, client_id: str = "vnmateai", client_secret: str = "S3CRET") -> None:
        self.client_id, self.client_secret = client_id, client_secret
        self.key, self.other_key = _rsa(), _rsa()
        self.kid = "kid-1"
        self.port = 0
        self.codes: Dict[str, Dict[str, Any]] = {}
        self.issuer_override: Optional[str] = None
        self.token_calls = 0
        self._server: Any = None
        self._thread: Optional[threading.Thread] = None

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def jwk(self) -> Dict[str, Any]:
        j = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**j, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def register(self, code: str, *, challenge: str, nonce: str, redirect_uri: str, claims: Optional[Dict[str, Any]] = None,
                 alg: str = "RS256", sign_with: str = "idp", drop: tuple = ()) -> None:
        """Chuẩn bị mã `code`: id_token mà token endpoint sẽ trả (có thể cố tình sai để thử các đòn tấn công)."""
        now = int(time.time())
        payload: Dict[str, Any] = {"iss": self.issuer, "aud": self.client_id, "sub": "user-1", "iat": now, "exp": now + 300, "nonce": nonce,
                                   "email": "an.nguyen@congty.vn", "email_verified": True, "name": "Nguyễn An", "groups": []}
        payload.update(claims or {})
        for k in drop:
            payload.pop(k, None)
        if alg == "none":
            token = jwt.encode(payload, None, algorithm="none", headers={"kid": self.kid})
        elif alg == "HS256":                                   # tấn công "đổi thuật toán": ký bằng client_secret như thể là IdP
            token = jwt.encode(payload, self.client_secret, algorithm="HS256", headers={"kid": self.kid})
        else:
            key = self.key if sign_with == "idp" else self.other_key
            token = jwt.encode(payload, key, algorithm=alg, headers={"kid": self.kid})
        self.codes[code] = {"challenge": challenge, "redirect_uri": redirect_uri, "token": token}

    def _app(self) -> FastAPI:
        app = FastAPI()
        me = self

        @app.get("/.well-known/openid-configuration")
        async def disco():
            return {"issuer": me.issuer_override or me.issuer, "authorization_endpoint": f"{me.issuer}/authorize",
                    "token_endpoint": f"{me.issuer}/token", "jwks_uri": f"{me.issuer}/jwks"}

        @app.get("/jwks")
        async def jwks():
            return {"keys": [me.jwk()]}

        @app.post("/token")
        async def token(request: Request):
            me.token_calls += 1
            form = dict((await request.form()).items())
            entry = me.codes.pop(form.get("code", ""), None)            # mã dùng MỘT lần
            if not entry:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            expected = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
            if expected != entry["challenge"] or form.get("redirect_uri") != entry["redirect_uri"] or form.get("client_id") != me.client_id:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE / redirect_uri / client_id không khớp"}, status_code=400)
            basic = request.headers.get("authorization", "")
            want = "Basic " + base64.b64encode(f"{me.client_id}:{me.client_secret}".encode()).decode()
            if basic != want:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            return {"access_token": "at-" + form["code"], "token_type": "Bearer", "id_token": entry["token"], "expires_in": 300}
        return app

    def start(self) -> "FakeOidc":
        import uvicorn
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self._server = uvicorn.Server(uvicorn.Config(self._app(), host="127.0.0.1", port=self.port, log_level="critical"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("IdP giả không khởi động được")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "FakeOidc":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
