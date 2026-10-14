# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devices/workstation_directory.py
===================================================
Một nơi duy nhất quyết định "máy này là máy nào" và "máy này có Agent chưa":

  - `norm_host`      chuẩn hoá tên máy để so khớp (chữ hoa / thường, hậu tố miền, `$` của tài khoản máy);
  - `resolve`        tên người dùng / AI nói -> mã agent đang kết nối (không đoán khi mơ hồ);
  - `agent_status`   trạng thái Agent của một tên máy: online / offline / revoked / none;
  - `coverage`       tóm tắt cho một danh sách máy (AD hoặc ERP).

Hàm THUẦN: nhận dữ liệu đã lấy (danh sách agent đang kết nối + danh sách agent đã đăng ký) nên không
phụ thuộc tầng giao diện; nơi gọi (bộ điều phối, router, skill) tự lấy dữ liệu.

Trạng thái:
  online   agent đang kết nối WebSocket — AI chạy công cụ trên máy này được
  offline  đã cài + đăng ký nhưng hiện không kết nối
  revoked  khoá của máy đã bị thu hồi
  none     chưa thấy agent nào khớp tên máy này (chưa cài, hoặc cài với tên khác)
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from mateai.infrastructure.directory.host_names import norm_host  # noqa: F401  (một bản chuẩn hoá duy nhất)

STATUS_LABEL = {
    "online": "Trực tuyến",
    "offline": "Đã cài · ngoại tuyến",
    "revoked": "Đã thu hồi khoá",
    "none": "Chưa cài",
}


def _keys(*names: Any) -> List[str]:
    out: List[str] = []
    for n in names:
        k = norm_host(n)
        if k and k not in out:
            out.append(k)
    return out


def build_index(connected: Iterable[Dict[str, Any]], enrolled: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Chỉ mục tra cứu: khoá chuẩn hoá -> danh sách mã agent (mơ hồ khi > 1)."""
    by_key: Dict[str, List[str]] = {}
    live: Dict[str, Dict[str, Any]] = {}
    reg: Dict[str, Dict[str, Any]] = {}
    for c in connected:
        cid = str(c.get("client_id") or "")
        if not cid:
            continue
        live[cid] = c
        for k in _keys(cid, c.get("hostname")):
            by_key.setdefault(k, [])
            if cid not in by_key[k]:
                by_key[k].append(cid)
    for d in enrolled:
        cid = str(d.get("client_id") or "")
        if not cid:
            continue
        reg[cid] = d
        for k in _keys(cid, d.get("hostname"), d.get("label")):
            by_key.setdefault(k, [])
            if cid not in by_key[k]:
                by_key[k].append(cid)
    return {"by_key": by_key, "live": live, "reg": reg}


def resolve(name: Any, index: Dict[str, Any], online_only: bool = True) -> Optional[str]:
    """Mã agent cho `name`. Khớp đúng mã trước; rồi không phân biệt hoa/thường / hậu tố miền.
    Nhiều agent cùng khớp -> None (không đoán — người gọi báo lỗi mơ hồ)."""
    raw = str(name or "").strip()
    if not raw:
        return None
    live = index["live"]
    if raw in live:
        return raw
    cands = index["by_key"].get(norm_host(raw), [])
    if online_only:
        cands = [c for c in cands if c in live]
    return cands[0] if len(cands) == 1 else None


def ambiguous(name: Any, index: Dict[str, Any]) -> List[str]:
    cands = [c for c in index["by_key"].get(norm_host(name), []) if c in index["live"]]
    return cands if len(cands) > 1 else []


def agent_status(name: Any, index: Dict[str, Any]) -> Dict[str, Any]:
    """{"status", "label", "client_id"} cho một tên máy."""
    cands = index["by_key"].get(norm_host(name), [])
    live = [c for c in cands if c in index["live"]]
    if live:
        status, cid = "online", live[0]
    else:
        regs = [index["reg"][c] for c in cands if c in index["reg"]]
        active = [r for r in regs if not r.get("revoked_at")]
        if active:
            status, cid = "offline", active[0]["client_id"]
        elif regs:
            status, cid = "revoked", regs[0]["client_id"]
        else:
            status, cid = "none", None
    return {"status": status, "label": STATUS_LABEL[status], "client_id": cid}


def coverage(hostnames: Iterable[Any], index: Dict[str, Any]) -> Dict[str, int]:
    """Đếm theo trạng thái cho một danh sách máy; `total` bỏ qua tên trống."""
    counts = {"online": 0, "offline": 0, "revoked": 0, "none": 0}
    total = 0
    for h in hostnames:
        if not str(h or "").strip():
            continue
        total += 1
        counts[agent_status(h, index)["status"]] += 1
    return {**counts, "total": total}


def online_list(index: Dict[str, Any], query: str = "") -> List[Dict[str, Any]]:
    """Máy đang trực tuyến (lọc theo chuỗi con của mã / tên máy / IP), cho AI và portal."""
    q = str(query or "").strip().lower()
    rows = []
    for cid, c in sorted(index["live"].items()):
        hay = " ".join(str(x or "") for x in (cid, c.get("hostname"), c.get("ip"))).lower()
        if q and q not in hay:
            continue
        rows.append({
            "client_id": cid, "hostname": c.get("hostname") or cid, "ip": c.get("ip"),
            "platform": c.get("platform"), "agent_version": c.get("agent_version"),
            "skills_count": c.get("skills_count"), "uptime": c.get("uptime"),
        })
    return rows
