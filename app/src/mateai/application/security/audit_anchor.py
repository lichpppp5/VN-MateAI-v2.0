# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/audit_anchor.py
===========================================
Neo đầu chuỗi băm audit RA NGOÀI CSDL (thẩm định thương mại 14/10/2026 #17).

Chuỗi băm phát hiện sửa / xoá một dòng — nhưng người có quyền ghi CSDL có thể sửa rồi TÍNH LẠI toàn bộ chuỗi. Neo: định kỳ ghi
(id dòng cuối, row_hash, số dòng, thời điểm) ra nơi CSDL không với tới (thư mục ổ khác `backup.offsite_dirs`, object storage /
S3 — bật Object Lock để thành WORM). Kiểm tra: mỗi neo cũ phải còn khớp đúng dòng đó trong CSDL; lệch = lịch sử bị viết lại.

Ghi neo: sau mỗi lần sao lưu (scripts/backup.py create) và `python scripts/audit_anchor.py anchor`.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LOCAL_REL = Path("backups") / "anchors" / "audit-anchors.jsonl"


def head() -> Optional[Dict[str, Any]]:
    """Dòng audit cuối CÓ băm: {id, row_hash, rows}."""
    from mateai.infrastructure.database import governance_repo
    return governance_repo.audit_head()


def _root() -> Path:
    from mateai.config.loader import settings
    return Path(settings.PROJECT_ROOT)


def _offsite_dirs() -> List[str]:
    from mateai.config.loader import get_config_section
    return [str(d) for d in (get_config_section("backup").get("offsite_dirs") or []) if str(d).strip()]


def anchor(store=None, extra_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ghi một neo vào tệp cục bộ + mọi thư mục ngoài + object storage. Trả neo và nơi đã ghi."""
    h = head()
    if not h:
        return {"anchored": False, "reason": "chưa có dòng audit có băm"}
    rec = {"at": datetime.now().isoformat(timespec="seconds"), **h}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    written, errors = [], []
    targets = [_root() / LOCAL_REL] + [Path(d) / "anchors" / "audit-anchors.jsonl" for d in (extra_dirs or _offsite_dirs())]
    for p in targets:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            written.append(str(p))
        except OSError as exc:
            errors.append(f"{p}: {exc}")
    try:
        if store is None:
            from mateai.infrastructure.files.object_storage import object_store
            store = object_store()
        key = f"anchors/audit-{rec['at'].replace(':', '')}.json"
        store.put(key, line.encode("utf-8"), content_type="application/json")
        written.append(f"object:{key}")
    except Exception as exc:  # noqa: BLE001 — object storage tắt / lỗi: vẫn còn bản ổ khác
        errors.append(f"object storage: {type(exc).__name__}: {exc}")
    return {"anchored": bool(written), "anchor": rec, "written": written, "errors": errors}


def read_local(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or (_root() / LOCAL_REL)
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def verify(anchors: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Mọi neo phải còn khớp: dòng `id` vẫn có đúng `row_hash`, và số dòng không giảm. Lệch → lịch sử bị viết lại / xoá."""
    from mateai.infrastructure.database import governance_repo
    anchors = anchors if anchors is not None else read_local()
    broken = []
    hashes, total = governance_repo.audit_hashes([int(a["id"]) for a in anchors])
    for a in anchors:
        current = hashes.get(int(a["id"]))
        if current is None or current != str(a["row_hash"]):
            broken.append({"anchor_at": a.get("at"), "id": a["id"], "reason": "dòng đã neo bị đổi / mất"})
        elif total < int(a.get("rows") or 0):
            broken.append({"anchor_at": a.get("at"), "id": a["id"], "reason": "số dòng ít hơn lúc neo (bị xoá)"})
    return {"ok": not broken, "anchors_checked": len(anchors), "broken": broken}
