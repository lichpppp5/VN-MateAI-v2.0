# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Neo chuỗi băm audit ra ngoài CSDL (src/mateai/application/security/audit_anchor.py).

    python scripts/audit_anchor.py anchor            # ghi neo ngay (sao lưu hằng đêm cũng tự ghi)
    python scripts/audit_anchor.py verify [tệp.jsonl]  # kiểm mọi neo (mặc định backups/anchors/audit-anchors.jsonl;
                                                     #   nên kiểm bằng bản ở ổ khác / S3 — bản cục bộ có thể bị sửa cùng CSDL)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    from mateai.application.security import audit_anchor
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("anchor", "verify"))
    ap.add_argument("file", nargs="?")
    a = ap.parse_args()
    if a.action == "anchor":
        res = audit_anchor.anchor()
    else:
        res = audit_anchor.verify(audit_anchor.read_local(Path(a.file)) if a.file else None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok", res.get("anchored")) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
