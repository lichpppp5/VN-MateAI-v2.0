# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/audit_export.py
===========================================
Báo cáo kiểm toán "AI đã làm gì, ai duyệt" xuất được cho kiểm toán viên (thẩm định thương mại 14/10/2026 #17).

  - CSV (UTF-8 có BOM — Excel mở đúng tiếng Việt), mỗi dòng audit một hàng, kèm prev_hash / row_hash;
  - HTML định dạng để IN (trình duyệt → In → Lưu PDF): đầu trang có kết quả kiểm tra toàn vẹn chuỗi băm, khoảng thời gian,
    người xuất, thống kê theo trạng thái / loại hành động; không cần thư viện PDF (tránh thêm phụ thuộc LGPL).
Chỉ đọc; chỉ admin gọi (routers/security.py). Nội dung `payload` đã che dữ liệu cá nhân trước khi ghi audit ở các đường mới;
bản xuất che thêm một lần (pii_guard.mask_text) — file có thể rời khỏi hệ thống.
"""
from __future__ import annotations

import csv
import html
import io
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

COLUMNS = ("id", "timestamp", "employee_id", "action_type", "status", "approved_by", "source_ip", "session_id",
           "payload", "prev_hash", "row_hash")
MAX_ROWS = 50_000


def fetch(date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    """Dòng audit trong khoảng [date_from, date_to] (YYYY-MM-DD, tính cả ngày cuối), cũ → mới."""
    from mateai.application.security.pii_guard import mask_text
    from mateai.infrastructure.database import governance_repo
    rows = governance_repo.audit_rows(date_from, date_to, max(1, min(int(limit), MAX_ROWS)))
    for r in rows:
        if r.get("payload"):
            r["payload"] = mask_text(str(r["payload"]))[0]
    return rows


def to_csv(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in COLUMNS})
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_html(rows: List[Dict[str, Any]], *, chain: Dict[str, Any], exported_by: str,
            date_from: Optional[str], date_to: Optional[str]) -> str:
    e = html.escape
    by_status = Counter(str(r.get("status") or "") for r in rows)
    by_action = Counter(str(r.get("action_type") or "") for r in rows).most_common(15)
    ok = chain.get("ok")
    verdict = ("<b style='color:#047857'>TOÀN VẸN</b> — chuỗi băm khớp từ đầu" if ok else
               f"<b style='color:#b91c1c'>PHÁT HIỆN SAI LỆCH</b> tại dòng {e(str(chain.get('first_broken_id')))}: "
               f"{e(str(chain.get('reason') or ''))}")
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{e(str(r.get(k) if r.get(k) is not None else ''))}</td>"
                         for k in ("id", "timestamp", "employee_id", "action_type", "status", "approved_by", "source_ip"))
        + f"<td class='p'>{e(str(r.get('payload') or '')[:400])}</td><td class='h'>{e(str(r.get('row_hash') or '')[:16])}</td></tr>"
        for r in rows)
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Báo cáo kiểm toán AI</title>
<style>body{{font:12px/1.45 system-ui,Segoe UI,Arial,sans-serif;color:#111;margin:24px}}h1{{font-size:18px;margin:0 0 4px}}
table{{border-collapse:collapse;width:100%;margin-top:10px}}th,td{{border:1px solid #ccc;padding:3px 5px;vertical-align:top;text-align:left}}
th{{background:#f1f5f9}}td.p{{max-width:420px;word-break:break-word;font-family:Consolas,monospace;font-size:10px}}
td.h{{font-family:Consolas,monospace;font-size:10px}}.meta td{{border:none;padding:1px 8px 1px 0}}
@media print{{body{{margin:8mm}}tr{{page-break-inside:avoid}}}}</style></head><body>
<h1>Báo cáo kiểm toán hành động AI và phê duyệt</h1>
<table class="meta"><tr><td>Khoảng thời gian</td><td>{e(date_from or 'từ đầu')} → {e(date_to or 'nay')}</td></tr>
<tr><td>Số dòng</td><td>{len(rows)}</td></tr><tr><td>Toàn vẹn chuỗi băm</td><td>{verdict}
 (đã kiểm {e(str(chain.get('verified')))} dòng; {e(str(chain.get('legacy_unhashed')))} dòng cũ chưa có băm)</td></tr>
<tr><td>Theo trạng thái</td><td>{e(', '.join(f'{k}: {v}' for k, v in sorted(by_status.items())))}</td></tr>
<tr><td>Hành động nhiều nhất</td><td>{e(', '.join(f'{k} ({v})' for k, v in by_action))}</td></tr>
<tr><td>Xuất bởi</td><td>{e(exported_by)} lúc {datetime.now():%d/%m/%Y %H:%M}</td></tr></table>
<p>Dữ liệu cá nhân trong cột nội dung đã được che. Mỗi dòng nối với dòng trước bằng băm SHA-256 (prev_hash → row_hash);
sửa hoặc xoá một dòng làm hỏng chuỗi từ dòng đó. Bản CSV đầy đủ băm: cùng màn hình, định dạng CSV.</p>
<table><thead><tr><th>#</th><th>Thời điểm</th><th>Người / tác nhân</th><th>Hành động</th><th>Trạng thái</th><th>Người duyệt</th>
<th>IP</th><th>Nội dung</th><th>Băm</th></tr></thead><tbody>{body_rows}</tbody></table></body></html>"""
