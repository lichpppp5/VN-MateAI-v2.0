# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/security_tls.py
====================
Phase 29: End-to-End SSL/TLS Encryption & PKI Management.

Responsibilities:
  - Automatic Self-Signed Certificate generation using cryptography.x509.
  - Multi-SAN support (localhost, 127.0.0.1, LAN IP, vnmate.local) to prevent hostname verification errors.
  - 10-year validity period for uninterrupted enterprise deployment.
  - Export public certificate for embedding into Client Agent distribution packages.
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("mateai.infrastructure.security.tls")

# Thư mục gốc dự án (đúng cả bản đóng gói) — không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
CERTS_DIR = _PROJECT_ROOT / "certs"
CERT_FILE = CERTS_DIR / "server.crt"
KEY_FILE = CERTS_DIR / "server.key"


def get_local_ip() -> str:
    """Detect LAN IP of current machine for TLS Certificate SAN injection."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def ensure_ssl_certs(
    certs_dir: Path = CERTS_DIR,
    cert_path: Path = CERT_FILE,
    key_path: Path = KEY_FILE,
    common_name: str = "vnmate.local",
    validity_days: int = 3650,
) -> Tuple[str, str]:
    """
    Ensure that a valid SSL certificate and private key exist on disk.
    If missing, automatically generates a self-signed X.509 certificate with SANs
    covering localhost, 127.0.0.1, the machine's LAN IP, and common_name.

    Returns:
        Tuple[str, str]: (absolute_cert_path, absolute_key_path)
    """
    certs_dir.mkdir(parents=True, exist_ok=True)

    if cert_path.exists() and key_path.exists() and cert_path.stat().st_size > 0 and key_path.stat().st_size > 0:
        logger.info("[TLS] Existing SSL certificate found: %s", cert_path)
        return str(cert_path), str(key_path)

    logger.info("[TLS] Generating new Self-Signed SSL Certificate (validity: %d days)...", validity_days)

    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        # 1. Generate RSA private key (2048-bit)
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )

        lan_ip = get_local_ip()
        hostname = socket.gethostname()

        # 2. Build Subject and Issuer (Self-Signed)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "VN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "HoChiMinh"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Enterprise"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "VN-MateAI Security"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Autonomous RPA"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name or lan_ip),
        ])

        # 3. Build Subject Alternative Names (SAN) for bulletproof SSL verification
        san_entries = [
            x509.DNSName("localhost"),
            x509.DNSName("vnmate.local"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv6Address("::1")),
        ]

        # Add machine hostname
        if hostname and hostname != "localhost":
            try:
                san_entries.append(x509.DNSName(hostname))
            except Exception:
                pass

        # Add LAN IP
        if lan_ip and lan_ip != "127.0.0.1":
            try:
                san_entries.append(x509.IPAddress(ipaddress.IPv4Address(lan_ip)))
            except Exception:
                pass

        now = datetime.datetime.now(datetime.timezone.utc)
        cert_builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=validity_days))
            .add_extension(
                x509.SubjectAlternativeName(san_entries),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None),
                critical=True,
            )
        )

        cert = cert_builder.sign(
            private_key=private_key,
            algorithm=hashes.SHA256(),
            backend=default_backend(),
        )

        # 4. Save Private Key (PEM format)
        key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_path.write_bytes(key_bytes)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass

        # 5. Save Certificate (PEM format)
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        cert_path.write_bytes(cert_bytes)

        logger.info(
            "[TLS] Successfully generated SSL Certificate!\n  - Cert: %s\n  - Key:  %s\n  - SANs: localhost, 127.0.0.1, %s, %s",
            cert_path, key_path, lan_ip, common_name
        )
        return str(cert_path), str(key_path)

    except Exception as exc:
        logger.error("[TLS] Failed to generate self-signed certificate: %s", exc)
        raise
