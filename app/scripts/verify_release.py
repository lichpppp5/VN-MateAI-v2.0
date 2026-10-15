# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Kiểm gói phát hành: chữ ký Ed25519 của SHA256SUMS.txt + băm từng tệp có mặt (thẩm định thương mại #25).

    python scripts/verify_release.py D:\VNmatebuildv1.0 [--pubkey vendor_signing.pub]

Mặc định dùng vendor_signing.pub TRONG gói — chỉ chứng minh gói nguyên vẹn so với khoá đó. Để chắc gói do nhà cung cấp phát
hành: so dấu vân tay in ra với dấu vân tay nhà cung cấp công bố qua kênh riêng (website / email / hợp đồng), hoặc truyền --pubkey.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def verify(bundle: Path, pubkey: Path = None) -> list:
    from mateai.infrastructure.security import signing
    problems = []
    pub = pubkey or (bundle / "vendor_signing.pub")
    sums, sig = bundle / "SHA256SUMS.txt", bundle / "SHA256SUMS.txt.sig"
    if not sig.is_file():
        problems.append("thiếu SHA256SUMS.txt.sig — gói chưa được ký")
    elif not signing.verify(sums.read_bytes(), sig.read_bytes(), signing.load_public_key(pub)):
        problems.append("CHỮ KÝ KHÔNG HỢP LỆ — SHA256SUMS.txt bị sửa hoặc ký bằng khoá khác")
    for ln in sums.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        digest, name = ln.split("  ", 1)
        f = bundle / name
        if not f.is_file():
            continue                                    # tệp không chép theo (vd image kiến trúc khác) — bỏ qua
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            problems.append(f"{name}: băm không khớp")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--pubkey")
    a = ap.parse_args()
    bundle = Path(a.bundle)
    pub = Path(a.pubkey) if a.pubkey else bundle / "vendor_signing.pub"
    from mateai.infrastructure.security import signing
    print("Dấu vân tay khoá công khai (SHA-256 DER):", signing.fingerprint(signing.load_public_key(pub)))
    problems = verify(bundle, pub)
    print("ĐẠT — gói nguyên vẹn, chữ ký hợp lệ" if not problems else "TRƯỢT:\n  " + "\n  ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
