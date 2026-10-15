# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Cấp tệp bản quyền cho khách hàng (chỉ chạy trên máy nhà cung cấp — cần certs/vendor_signing.key).

    python scripts/issue_license.py --customer "Công ty A" --users 50 --workstations 200 --expires 2027-12-31 \
        --out licenses/cong-ty-a.json

Gửi tệp cho khách hàng; quản trị viên nạp ở Portal (PUT /api/v1/license) hoặc chép vào certs/license.json rồi khởi động lại.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def issue(customer: str, users: int, workstations: int, expires: str, edition: str = "commercial") -> dict:
    from mateai.application.licensing import canonical
    from mateai.infrastructure.security import signing
    datetime.strptime(expires, "%Y-%m-%d")
    lic = {"customer": customer, "license_id": "LIC-" + uuid.uuid4().hex[:12].upper(), "edition": edition,
           "max_users": int(users), "max_workstations": int(workstations), "expires": expires,
           "issued_at": date.today().isoformat()}
    return {"license": lic, "signature": signing.b64(signing.sign(canonical(lic)))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", required=True)
    ap.add_argument("--users", type=int, required=True)
    ap.add_argument("--workstations", type=int, required=True)
    ap.add_argument("--expires", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = issue(a.customer, a.users, a.workstations, a.expires)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã cấp {doc['license']['license_id']} cho {a.customer} → {out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
