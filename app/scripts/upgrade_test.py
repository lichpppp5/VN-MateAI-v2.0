# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/upgrade_test.py
=======================
Kiểm thử NÂNG CẤP thật trên Docker (thẩm định thương mại 14/10/2026 #13): bản N đang chạy có dữ liệu → khách giải nén gói
N+1 đè lên (giữ .env và volume) → `docker compose up -d` → dữ liệu, tài khoản, chuỗi audit và schema phải còn nguyên.

    python scripts/upgrade_test.py --from-bundle D:\\VNmatebuildv1.0            # N = gói đã phát hành, N+1 = HEAD (build image)
    python scripts/upgrade_test.py --from-bundle D:\\VNmatebuildv1.0 --to-image vnmateai/app:2.0.0   # image N+1 có sẵn

Chạy trên cổng thử (18443 / 18000) với dự án compose riêng (`vnmateupg`) — không đụng máy chủ thật; xong thì xoá container và
volume (giữ lại bằng --keep). Mã thoát 0 = nâng cấp đạt.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import shutil
import ssl
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "vnmateupg"
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


def say(msg: str) -> None:
    print(f"[upgrade-test] {msg}", flush=True)


def run(cmd, cwd=None, check=True, quiet=False) -> subprocess.CompletedProcess:
    env = {**os.environ, "COMPOSE_PROJECT_NAME": PROJECT}
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=quiet, text=True, encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise SystemExit(f"Lệnh lỗi ({res.returncode}): {' '.join(map(str, cmd))}\n{(res.stdout or '')[-2000:]}{(res.stderr or '')[-2000:]}")
    return res


# ── HTTP ─────────────────────────────────────────────────────────────────────

class Api:
    def __init__(self, port: int):
        self.base = f"https://127.0.0.1:{port}"

    def call(self, method: str, path: str, body=None, token: str = ""):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, context=_SSL, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode("utf-8", "replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            return 0, str(e)

    def wait(self, path: str, timeout_s: float = 420) -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            code, _ = self.call("GET", path)
            if code == 200:
                return
            time.sleep(4)
        raise SystemExit(f"{path} chưa trả 200 sau {timeout_s:.0f}s")


def _totp(secret: str, at: float) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", int(at // 30)), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def login(api: Api, user: str, password: str, state: dict) -> str:
    """Đăng nhập qua mọi bước bản mới có thể đòi: đổi mật khẩu, cài MFA bắt buộc, nhập mã MFA. Trả access token."""
    code, r = api.call("POST", "/api/v1/login", {"username": user, "password": password})
    for _ in range(4):
        if code != 200:
            raise SystemExit(f"Đăng nhập {user} lỗi {code}: {r}")
        st = r.get("status")
        if st == "success":
            return r["access_token"]
        if st == "password_change_required":
            password = state.setdefault(f"pw:{user}", "Nang" + secrets.token_hex(8) + "9")
            code, r = api.call("POST", "/api/v1/login/change-password", {"change_token": r["change_token"], "new_password": password})
            if code == 200 and r.get("status") not in ("success", "mfa_required", "mfa_setup_required"):
                code, r = api.call("POST", "/api/v1/login", {"username": user, "password": password})
        elif st == "mfa_setup_required":
            tok = r["setup_token"]
            code, s = api.call("POST", "/api/v1/auth/mfa/setup", {}, tok)
            if code != 200:
                raise SystemExit(f"Cài MFA lỗi {code}: {s}")
            state[f"mfa:{user}"] = s["secret"]
            code, r = api.call("POST", "/api/v1/auth/mfa/enable", {"code": _totp(s["secret"], time.time())}, tok)
            if code == 200 and not r.get("access_token"):
                code, r = api.call("POST", "/api/v1/login", {"username": user, "password": password})
            elif code == 200:
                return r["access_token"]
        elif st == "mfa_required":
            time.sleep(31 - time.time() % 30)       # mỗi bước 30 s chỉ dùng một lần (chống phát lại) — sang bước mới
            code, r = api.call("POST", "/api/v1/login/mfa", {"mfa_token": r["mfa_token"], "code": _totp(state[f"mfa:{user}"], time.time())})
        else:
            raise SystemExit(f"Đăng nhập {user}: trạng thái lạ {r}")
    raise SystemExit(f"Đăng nhập {user}: quá nhiều bước")


def password_of(state: dict, user: str, initial: str) -> str:
    return state.get(f"pw:{user}", initial)


# ── Các bước ─────────────────────────────────────────────────────────────────

def env_get(env: Path, key: str) -> str:
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def env_set(env: Path, key: str, value: str) -> None:
    lines = env.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_old_image(bundle: Path, tag: str) -> None:
    if run(["docker", "image", "inspect", tag], check=False, quiet=True).returncode == 0:
        return
    tars = [t for t in (bundle / "images").glob("*.tar") if "-arm64-" not in t.name]
    if not tars:
        raise SystemExit(f"Không có image {tag} và không thấy images/*.tar trong {bundle}")
    say(f"Nạp image bản cũ từ {tars[0].name} …")
    run(["docker", "load", "-i", str(tars[0])], quiet=True)


def build_new_image(tag: str) -> None:
    """Image N+1 từ HEAD (giống build_release: git archive — chỉ mã đã commit)."""
    tmp = Path(tempfile.mkdtemp(prefix="vnmate-upg-src-"))
    try:
        data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
        say(f"Build image {tag} từ HEAD (lần đầu 15–30 phút) …")
        run(["docker", "build", "-f", "deploy/docker/Dockerfile", "-t", tag, "."], cwd=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compose(work: Path, *args, check=True, quiet=True):
    return run(["docker", "compose", "-p", PROJECT, *args], cwd=work, check=check, quiet=quiet)


def snapshot(api: Api, token: str) -> dict:
    code, users = api.call("GET", "/api/v1/users", token=token)
    if code != 200:
        raise SystemExit(f"/api/v1/users lỗi {code}: {users}")
    rows = users.get("users", users) if isinstance(users, dict) else users
    code, chain = api.call("GET", "/api/v1/security/audit-logs/verify", token=token)
    if code != 200:
        raise SystemExit(f"audit verify lỗi {code}: {chain}")
    return {"users": sorted(str(u.get("username")) for u in rows), "audit": chain}


def _n(chain: dict) -> int:
    return int(chain.get("verified") or 0) + int(chain.get("legacy_unhashed") or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-bundle", required=True, type=Path, help="thư mục gói bản N (có docker-compose.yml, env.example, images/)")
    ap.add_argument("--to-image", default="", help="image bản N+1 có sẵn; bỏ trống = build từ HEAD")
    ap.add_argument("--https-port", type=int, default=18443)
    ap.add_argument("--iot-port", type=int, default=18000)
    ap.add_argument("--keep", action="store_true", help="giữ container + volume sau khi chạy (để soi)")
    a = ap.parse_args()

    bundle = a.from_bundle.resolve()
    old_ver = (bundle / "VERSION.txt").read_text(encoding="utf-8").split()[1] if (bundle / "VERSION.txt").is_file() else "1.0.0"
    old_tag = f"vnmateai/app:{old_ver}"
    new_tag = a.to_image or "vnmateai/app:upgrade-test"
    new_ver = new_tag.split(":", 1)[1]
    ensure_old_image(bundle, old_tag)
    if not a.to_image:
        build_new_image(new_tag)

    work = Path(tempfile.mkdtemp(prefix="vnmate-upg-"))
    compose_new = ROOT / "deploy" / "docker" / "docker-compose.yml"
    api = Api(a.https_port)
    state: dict = {}
    ok = False
    try:
        # 1. Bản N: cài như khách (gói cũ, .env sinh mật khẩu)
        shutil.copy2(bundle / "docker-compose.yml", work / "docker-compose.yml")
        shutil.copy2(bundle / "env.example", work / ".env")
        env = work / ".env"
        for k in ("VNMATE_ADMIN_PASSWORD", "VNMATE_MANAGER_PASSWORD", "VNMATE_VIEWER_PASSWORD", "VNMATE_PG_PASSWORD", "VNMATE_REDIS_PASSWORD"):
            env_set(env, k, "Upg" + secrets.token_hex(10) + "7")
        env_set(env, "VNMATE_VERSION", old_ver)
        env_set(env, "VNMATE_HTTPS_PORT", str(a.https_port))
        env_set(env, "VNMATE_IOT_PORT", str(a.iot_port))
        compose(work, "down", "-v", check=False)
        say(f"Khởi động bản {old_ver} trên cổng {a.https_port} …")
        compose(work, "up", "-d")
        api.wait("/livez")
        admin_pw = env_get(env, "VNMATE_ADMIN_PASSWORD")
        tok = login(api, "admin", admin_pw, state)

        # 2. Dữ liệu của khách trên bản N
        probe_user, probe_pw = f"upg_{secrets.token_hex(3)}", "Thu" + secrets.token_hex(6) + "5"
        code, r = api.call("POST", "/api/v1/users", {"username": probe_user, "password": probe_pw, "full_name": "Nâng cấp thử",
                                                     "role": "viewer"}, tok)
        if code != 200:
            raise SystemExit(f"Tạo người dùng trên bản {old_ver} lỗi {code}: {r}")
        login(api, probe_user, probe_pw, state)                 # thêm dòng audit đăng nhập
        before = snapshot(api, tok)
        say(f"Bản {old_ver}: {len(before['users'])} tài khoản, audit {_n(before['audit'])} dòng, "
            f"chuỗi {'nguyên vẹn' if before['audit'].get('ok') else 'HỎNG'}")

        # 3. Nâng cấp: gói mới đè lên — compose mới, GIỮ .env và volume (đúng như install.sh: .env có rồi thì không tạo lại)
        shutil.copy2(compose_new, work / "docker-compose.yml")
        env_set(env, "VNMATE_VERSION", new_ver)
        say(f"Nâng cấp lên {new_tag} …")
        compose(work, "up", "-d")
        api.wait("/livez")
        api.wait("/readyz")

        # 4. Kiểm sau nâng cấp
        tok = login(api, "admin", password_of(state, "admin", admin_pw), state)
        after = snapshot(api, tok)
        login(api, probe_user, password_of(state, probe_user, probe_pw), state)
        code, startup = api.call("GET", "/api/v1/health/startup", token=tok)
        schema = (startup.get("steps") or {}).get("schema_migrations", {}).get("detail") or "?" if isinstance(startup, dict) else None
        problems = []
        missing = sorted(set(before["users"]) - set(after["users"]))
        if missing:
            problems.append(f"mất tài khoản: {missing}")
        if not after["audit"].get("ok"):
            problems.append(f"chuỗi audit hỏng sau nâng cấp: {json.dumps(after['audit'], ensure_ascii=False)[:400]}")
        n_before, n_after = _n(before["audit"]), _n(after["audit"])
        if n_after < n_before:
            problems.append(f"audit ít dòng hơn trước ({n_after} < {n_before})")
        if code != 200:
            problems.append(f"/api/v1/health/startup lỗi {code}")
        elif isinstance(startup, dict) and startup.get("failed"):
            problems.append("bước khởi động lỗi: " + ", ".join(startup["failed"]))
        say(f"Bản {new_ver}: {len(after['users'])} tài khoản, audit {n_after} dòng, schema={schema}")
        if problems:
            for p in problems:
                say("LỖI: " + p)
            compose(work, "logs", "--tail=80", "app", check=False, quiet=False)
            return 1
        ok = True
        say(f"ĐẠT: nâng cấp {old_ver} → {new_ver} giữ nguyên tài khoản, mật khẩu, chuỗi audit; hệ thống sẵn sàng.")
        return 0
    finally:
        if a.keep:
            say(f"Giữ môi trường thử ở {work} (dự án compose '{PROJECT}'). Dọn: docker compose -p {PROJECT} down -v")
        else:
            compose(work, "down", "-v", check=False)
            shutil.rmtree(work, ignore_errors=True)
        if not ok:
            say("KHÔNG ĐẠT")


if __name__ == "__main__":
    sys.exit(main())
