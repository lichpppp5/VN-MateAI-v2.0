# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Khoá ký của nhà cung cấp (Ed25519) — dùng cho bản quyền và gói phát hành.

    python scripts/vendor_keys.py init      # tạo certs/vendor_signing.key (bí mật) + deploy/keys/vendor_signing.pub (commit)
    python scripts/vendor_keys.py show      # in khoá công khai + dấu vân tay

Khoá riêng: SAO LƯU nơi an toàn (két / HSM / trình quản lý bí mật). Mất khoá = không cấp được bản quyền mới khớp các máy đã cài;
lộ khoá = ai cũng tự cấp được bản quyền → tạo khoá mới, phát hành bản có khoá công khai mới, cấp lại bản quyền.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from mateai.infrastructure.security import signing
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("init", "show"))
    a = ap.parse_args()
    priv, pub = ROOT / signing.PRIVKEY_REL, ROOT / signing.PUBKEY_REL
    if a.action == "init":
        signing.generate(priv, pub)
        print(f"Đã tạo {priv} (BÍ MẬT — sao lưu, không commit) và {pub} (commit).")
    data = pub.read_bytes()
    print(data.decode("ascii").strip())
    print("Dấu vân tay SHA-256 (DER) — công bố cho khách hàng:", signing.fingerprint(signing.load_public_key(pub)))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
