# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Danh mục kỹ năng có CHỦ SỞ HỮU và KIỂM THỬ (thẩm định thương mại 14/10/2026 #3) → skills/catalog.json.

    python scripts/skill_catalog.py           # in bảng
    python scripts/skill_catalog.py --write   # ghi skills/catalog.json (chạy lại khi thêm / đổi kỹ năng)

Mỗi kỹ năng: nhóm (core | integration | demo | personal), đội chủ sở hữu, có test nhắc tên kỹ năng trong tests/ không,
có trong bản thương mại không (mateai.config.edition). tests/test_skill_catalog.py giữ tệp khớp với registry.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

#: module → (nhóm, đội chủ sở hữu)
OWNERS = {
    "pc_control_skills": ("core", "Vận hành CNTT"), "monitoring_skills": ("core", "Vận hành CNTT"),
    "sysadmin_skills": ("core", "Vận hành CNTT"), "file_system": ("core", "Vận hành CNTT"),
    "custom_skills": ("core", "Vận hành CNTT"), "workstation_tools": ("core", "Vận hành CNTT"),
    "auto_check_cpu_ram_performance": ("core", "Vận hành CNTT"), "auto_get_server_uptime": ("core", "Vận hành CNTT"),
    "itsm_skills": ("core", "Vận hành CNTT"), "playbook_tools": ("core", "Vận hành CNTT"),
    "orchestration_tools": ("core", "Nền tảng AI"),
    "infra_monitor_tools": ("integration", "Tích hợp"), "integration_tools": ("integration", "Tích hợp"),
    "data_source_tools": ("integration", "Tích hợp"), "domain_alert_skills": ("integration", "Tích hợp"),
    "ninerouter_skills": ("integration", "Nền tảng AI"),
    "llmops_tools": ("core", "Nền tảng AI"), "meta_skills": ("core", "Nền tảng AI"), "ai_delegation": ("core", "Nền tảng AI"),
    "agent_orchestrator": ("core", "Nền tảng AI"), "visual_skills": ("core", "Nền tảng AI"),
    "enterprise_rag": ("core", "Dữ liệu doanh nghiệp"), "graph_rag": ("core", "Dữ liệu doanh nghiệp"),
    "erp_organization": ("core", "Dữ liệu doanh nghiệp"), "analytics_engine": ("core", "Dữ liệu doanh nghiệp"),
    "proactive_manager": ("core", "Nhân sự & giao việc"), "lean_hr_skills": ("core", "Nhân sự & giao việc"),
    "onboarding_workflow": ("core", "Nhân sự & giao việc"), "ethics_tools": ("core", "Nhân sự & giao việc"),
    "business_tools": ("demo", "Demo"), "email_gateway": ("demo", "Demo"), "dev_fleet_tools": ("demo", "Nội bộ đội phát triển"),
    "robotics_tools": ("demo", "Robot"), "computer_use_skills": ("demo", "Nền tảng AI"),
    "auto_open_youtube": ("personal", "Cá nhân"), "auto_play_music": ("personal", "Cá nhân"),
    "auto_get_hanoi_weather": ("personal", "Cá nhân"), "auto_count_chrome_tabs": ("personal", "Cá nhân"),
    "excel_records_skill": ("personal", "Cá nhân"), "auto_open_notepad_file": ("personal", "Cá nhân"),
}


def build() -> dict:
    from mateai.config import edition
    reg = json.loads((ROOT / "skills" / "registry.json").read_text(encoding="utf-8"))
    test_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                          for p in (ROOT / "tests").rglob("*") if p.suffix in (".py", ".mjs"))
    out = {}
    for name, entry in sorted(reg.items()):
        module = str(entry.get("module") or "").split(".")[-1]
        tier, owner = OWNERS.get(module, ("core" if not module.startswith("auto_") else "personal", "Chưa gán"))
        if name in edition.DEMO_SKILLS and tier in ("core", "integration"):
            tier = "demo"
        out[name] = {
            "module": module, "tier": tier, "owner": owner,
            "tested": bool(re.search(rf"\b{re.escape(name)}\b", test_text)),
            "commercial": name not in edition.DEMO_SKILLS and name not in edition.CODEGEN_TOOLS,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    cat = build()
    if a.write:
        (ROOT / "skills" / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    untested = [n for n, c in cat.items() if c["commercial"] and not c["tested"]]
    print(f"{len(cat)} kỹ năng · bản thương mại {sum(c['commercial'] for c in cat.values())} · "
          f"chưa có test (trong bản thương mại) {len(untested)}: {', '.join(untested)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
