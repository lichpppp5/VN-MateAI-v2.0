# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/infrastructure/security/signing.py
=========================================
Chữ ký số Ed25519 của NHÀ CUNG CẤP — một nơi cho: tệp bản quyền (licensing), gói phát hành (SHA256SUMS.txt.sig), neo audit.

  - Khoá riêng: `certs/vendor_signing.key` (máy nhà cung cấp, KHÔNG vào git / gói cài) — tạo bằng `python scripts/vendor_keys.py init`.
  - Khoá công khai: `deploy/keys/vendor_signing.pub` (vào git và gói cài) — máy khách hàng chỉ kiểm, không ký được.
  - `VNMATEAI_VENDOR_PUBKEY` (đường dẫn PEM) đổi khoá công khai khi cần (thử nghiệm / xoay khoá).
Kiểm chữ ký ngoài hệ thống: `openssl pkeyutl -verify -pubin -inkey vendor_signing.pub -rawin -in <tệp> -sigfile <tệp>.sig`.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PUBKEY_REL = Path("deploy") / "keys" / "vendor_signing.pub"
PRIVKEY_REL = Path("certs") / "vendor_signing.key"


def _root() -> Path:
    from mateai.config.loader import settings
    return Path(settings.PROJECT_ROOT)


def public_key_path() -> Path:
    env = os.environ.get("VNMATEAI_VENDOR_PUBKEY", "").strip()
    return Path(env) if env else _root() / PUBKEY_REL


def load_public_key(path: Optional[Path] = None) -> Optional[Ed25519PublicKey]:
    p = Path(path) if path else public_key_path()
    if not p.is_file():
        return None
    key = serialization.load_pem_public_key(p.read_bytes())
    return key if isinstance(key, Ed25519PublicKey) else None


def load_private_key(path: Optional[Path] = None) -> Optional[Ed25519PrivateKey]:
    p = Path(path) if path else _root() / PRIVKEY_REL
    if not p.is_file():
        return None
    key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    return key if isinstance(key, Ed25519PrivateKey) else None


def generate(private_path: Path, public_path: Path) -> None:
    """Tạo cặp khoá mới. Không ghi đè khoá riêng đã có (mất khoá cũ = bản quyền đã cấp không kiểm được nữa)."""
    if private_path.exists():
        raise FileExistsError(f"{private_path} đã có — không ghi đè khoá ký")
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    try:
        os.chmod(private_path, 0o600)
    except OSError:
        pass
    public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo))


def sign(data: bytes, key: Optional[Ed25519PrivateKey] = None) -> bytes:
    key = key or load_private_key()
    if key is None:
        raise FileNotFoundError(f"Không có khoá ký {PRIVKEY_REL} — chỉ máy nhà cung cấp ký được")
    return key.sign(data)


def verify(data: bytes, signature: bytes, key: Optional[Ed25519PublicKey] = None) -> bool:
    key = key or load_public_key()
    if key is None:
        return False
    try:
        key.verify(signature, data)
        return True
    except InvalidSignature:
        return False


def fingerprint(key: Ed25519PublicKey) -> str:
    """SHA-256 của khoá công khai dạng DER (SubjectPublicKeyInfo) — cùng cách tính với install.sh (openssl ... -outform DER)."""
    import hashlib
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def b64(sig: bytes) -> str:
    return base64.b64encode(sig).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(str(text or "").encode("ascii"), validate=True)
