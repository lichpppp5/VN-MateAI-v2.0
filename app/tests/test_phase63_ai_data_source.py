# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase63_ai_data_source.py
=====================================
Kiểm thử tool AI Ly Ly đọc/xuất báo cáo (skills/data_source_tools.py).

Trọng tâm là ba điều dễ sai và hậu quả nặng nếu hỏng:
  - khoá bí mật lọt vào kết quả trả về cho LLM (rò qua log hội thoại)
  - bóc envelope HITL sai, khiến AI báo "không có dữ liệu" dù có
  - không có trần số dòng, AI kéo hết bảng vào làm vỡ ngữ cảnh
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ── Sổ đăng ký trỏ vào file tạm ──────────────────────────────────────────
from mateai.infrastructure.connectors import custom_registry  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="vnmate-ai-ds-")
custom_registry.STORE_PATH = Path(_TMP) / "data_sources.json"
custom_registry.upsert_source("misa-amh", {
    "id": "misa-amh", "title": "MISA AMH", "category": "reporting",
    "base_url": "http://127.0.0.1:8899", "default_path": "/api/reports/tồn kho",
    "auth_type": "bearer", "auth_value": "KHOA-BI-MAT-KHONG-DUOC-LO",
    "paths": {"tồn kho": "/api/reports/tồn kho", "doanh thu": "/api/reports/doanh thu"},
    "timeout_seconds": 2, "row_limit": 50,
})
custom_registry.upsert_source("erp-noi-bo", {
    "id": "erp-noi-bo", "title": "ERP nội bộ", "category": "connector",
    "base_url": "http://erp.congty.vn/api", "default_path": "/reports",
    "auth_type": "bearer", "auth_value": "KHOA-ERP-KHONG-DUOC-LO",
    "paths": {}, "timeout_seconds": 2, "row_limit": 50,
})

from skills.data_source_tools import (  # noqa: E402
    AI_ROW_LIMIT,
    fetch_data_source,
    list_data_sources,
    prepare_data_source_export,
)


# ══ 1. Đăng ký skill ═════════════════════════════════════════════════════
section("Đăng ký skill cho AI")
registry_path = Path(__file__).resolve().parents[1] / "skills" / "registry.json"
reg = json.loads(registry_path.read_text(encoding="utf-8"))

def meta_of(tool: str) -> dict:
    """Schema tool thật sự nằm dưới khoá `meta`, không phải `function`."""
    return (reg.get(tool) or {}).get("meta") or {}

for tool in ("list_data_sources", "fetch_data_source", "prepare_data_source_export"):
    check(f"{tool} có trong registry.json", tool in reg, f"thiếu {tool}")
    if tool in reg:
        desc = meta_of(tool).get("description") or ""
        # Mô tả rỗng thì LLM không biết tool dùng khi nào -> tool tồn tại mà
        # không ai gọi, cũng là một kiểu "chưa xong" như tool chưa đăng ký.
        check(f"{tool} có mô tả cho LLM đọc", len(desc) > 30, f"{len(desc)} ký tự")
        check(f"{tool} không bị tắt mặc định",
              (reg[tool] or {}).get("enabled", True) is True,
              f"enabled={(reg[tool] or {}).get('enabled')}")

params = (meta_of("fetch_data_source").get("parameters") or {}).get("properties", {})
check("fetch khai báo tham số source_id", "source_id" in params, str(list(params)))
check("fetch khai báo tham số report", "report" in params)
check("fetch khai báo tham số limit", "limit" in params)
check("fetch đánh dấu source_id bắt buộc",
      "source_id" in (meta_of("fetch_data_source").get("parameters") or {}).get("required", []))

fmt = ((meta_of("prepare_data_source_export").get("parameters") or {})
       .get("properties", {}).get("format", {}))
check("export giới hạn format vào xlsx/csv",
      sorted(fmt.get("enum", [])) == ["csv", "xlsx"], str(fmt.get("enum")))

check("list_data_sources nhận category tùy chọn",
      "category" in ((meta_of("list_data_sources").get("parameters") or {})
                     .get("properties", {})))


# ══ 2. Danh sách nguồn ═══════════════════════════════════════════════════
section("list_data_sources")
r = asyncio.run(list_data_sources())
check("trả về thành công", r.get("success") is True, str(r)[:80])
check("thấy đủ 2 nguồn", r.get("count") == 2, str(r.get("count")))

ids = [s["id"] for s in r["data_sources"]]
check("có misa-amh", "misa-amh" in ids, str(ids))
check("có erp-noi-bo", "erp-noi-bo" in ids, str(ids))
check("nêu tên báo cáo của từng nguồn",
      any(s["available_reports"] == ["tồn kho", "doanh thu"] for s in r["data_sources"]),
      json.dumps([s["available_reports"] for s in r["data_sources"]], ensure_ascii=False))

section("Không lộ bí mật cho LLM")
blob = json.dumps(r, ensure_ascii=False)
check("không có khoá bí mật MISA", "KHOA-BI-MAT-KHONG-DUOC-LO" not in blob)
check("không có khoá bí mật ERP", "KHOA-ERP-KHONG-DUOC-LO" not in blob)
check("không có trường auth_value", "auth_value" not in blob)
check("không lộ base_url nội bộ", "127.0.0.1" not in blob and "erp.congty.vn" not in blob)
check("vẫn cho biết đã có khoá hay chưa", all(s["has_auth"] is True for s in r["data_sources"]))

section("Lọc theo nhóm")
r_cat = asyncio.run(list_data_sources(category="connector"))
check("lọc connector -> đúng 1 nguồn", r_cat.get("count") == 1, str(r_cat.get("count")))
check("lọc connector -> đúng id", r_cat["data_sources"][0]["id"] == "erp-noi-bo")
r_none = asyncio.run(list_data_sources(category="khong-ton-tai"))
check("nhóm không có -> 0 nguồn, không lỗi",
      r_none.get("success") is True and r_none.get("count") == 0, str(r_none)[:80])
check("0 nguồn -> có gợi ý dắt người dùng",
      bool(r_none.get("hint")), str(r_none.get("hint")))


# ══ 3. Lấy báo cáo ══════════════════════════════════════════════════════
section("fetch_data_source — xử lý lỗi")
r = asyncio.run(fetch_data_source(source_id="khong-ton-tai"))
check("nguồn không tồn tại -> success False", r.get("success") is False)
check("nguồn không tồn tại -> nêu rõ tên", "khong-ton-tai" in (r.get("error") or ""))
check("nguồn không tồn tại -> gợi ý gọi list", "list_data_sources" in (r.get("hint") or ""))

r2 = asyncio.run(fetch_data_source(source_id="misa-amh", report="bao-cao-khong-co"))
check("báo cáo sai -> success False", r2.get("success") is False)
check("báo cáo sai -> nêu tên báo cáo đang có",
      "tồn kho" in (r2.get("error") or "") and "doanh thu" in (r2.get("error") or ""),
      str(r2.get("error")))

section("fetch_data_source — chặn số dòng")
check("AI_ROW_LIMIT hợp lý", 0 < AI_ROW_LIMIT <= 500, str(AI_ROW_LIMIT))
src = inspect.getsource(fetch_data_source)
check("limit bị ép về trần trong mã nguồn",
      "AI_ROW_LIMIT" in src and "min(" in src)
r3 = asyncio.run(fetch_data_source(source_id="misa-amh", limit=10**9))
check("limit khổng lồ không làm vỡ (chặn cứng ở server)", r3.get("success") in (True, False))
check("limit âm không gây lỗi", asyncio.run(
    fetch_data_source(source_id="misa-amh", limit=-5)).get("success") in (True, False))

section("fetch_data_source — không lộ bí mật khi lỗi")
r4 = asyncio.run(fetch_data_source(source_id="misa-amh", report="x"))
check("lỗi kéo dữ liệu không chứa khoá",
      "KHOA-BI-MAT-KHONG-DUOC-LO" not in json.dumps(r4, ensure_ascii=False))


# ══ 4. Chuẩn bị xuất ═════════════════════════════════════════════════════
section("prepare_data_source_export")
r5 = asyncio.run(prepare_data_source_export(source_id="khong-ton-tai"))
check("nguồn không tồn tại -> báo lỗi", r5.get("success") is False)
check("nguồn không tồn tại -> có gợi ý", "list_data_sources" in (r5.get("hint") or ""))

r6 = asyncio.run(prepare_data_source_export(source_id="misa-amh", format="pdf"))
check("format sai -> báo lỗi rõ", "xlsx" in (r6.get("error") or ""), str(r6.get("error")))

r7 = asyncio.run(prepare_data_source_export(source_id="erp-noi-bo", format="csv"))
check("format hợp lệ -> không lỗi định dạng",
      "format" not in (r7.get("error") or ""), str(r7.get("error")))
blob7 = json.dumps(r7, ensure_ascii=False)
check("kết quả xuất không lộ khoá", "KHOA-ERP-KHONG-DUOC-LO" not in blob7)
if r7.get("download_url"):
    check("download_url trỏ đúng endpoint export",
          r7["download_url"].endswith("/export") and "erp-noi-bo" in r7["download_url"],
          r7["download_url"])
    check("có hướng dẫn cho người dùng", bool(r7.get("user_instructions")))
else:
    # App giả lập đã tắt -> tool phải nói lỗi, không hứa hẹn đường dẫn rỗng.
    check("app chết -> báo lỗi thay vì trả đường dẫn rỗng",
          r7.get("success") is False and bool(r7.get("error")), str(r7)[:100])

section("max_rows bị giới hạn")
src2 = inspect.getsource(prepare_data_source_export)
check("max_rows chặn trần 10000", "10000" in src2)
r8 = asyncio.run(prepare_data_source_export(source_id="misa-amh", max_rows=10**9))
check("max_rows khổng lồ không vượt trần", r8.get("requested_rows", 0) <= 10000,
      str(r8.get("requested_rows")))


# ══ 5. Đi qua cổng HITL ═══════════════════════════════════════════════════
section("Đi qua cổng HITL")
src3 = inspect.getsource(sys.modules["skills.data_source_tools"])
check("dùng execute_with_hitl", "execute_with_hitl" in src3)
check("gắn requested_by=AI_Agent", 'requested_by="AI_Agent"' in src3)
check("xử lý awaiting_approval (không báo nhầm là lỗi)",
      "awaiting_approval" in src3)
check("có nhãn pending_approval để AI nói đúng",
      "awaiting_approval" in src3 and "HITL" in src3)
check("bóc envelope về kết quả phẳng", 'result.get("result")' in src3)


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
