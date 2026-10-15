# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/setup_mcp_servers.py
============================
Cài và đăng ký các MCP server tham chiếu chính thức (modelcontextprotocol/servers) đã rà soát và đóng gói trong
`vendor/mcp-servers/` — xem docs/integrations/mcp-reference-servers.md.

    python scripts/setup_mcp_servers.py --install          # tạo .mcp-venv, cài server, đăng ký vào cổng MCP
    python scripts/setup_mcp_servers.py                    # chỉ đăng ký lại (venv đã có) — chạy lặp lại an toàn
    python scripts/setup_mcp_servers.py --dry-run          # in cấu hình sẽ ghi, không ghi gì
    python scripts/setup_mcp_servers.py --no-git           # bỏ server git (vd máy chủ không có mã nguồn Git)

Server chạy trong MÔI TRƯỜNG PYTHON RIÊNG (`.mcp-venv`, Docker: `/opt/mcp-venv`): thư viện `mcp` kéo starlette / uvicorn bản khác,
không được lẫn vào môi trường của VN-MateAI (fastapi / starlette ghim). Phiên bản thư viện ghim ở `vendor/mcp-servers/constraints.txt`.

| server | công cụ                                   | rủi ro                    | ghi chú                                            |
|--------|-------------------------------------------|---------------------------|----------------------------------------------------|
| time   | get_current_time, convert_time            | 1                         | múi giờ mặc định Asia/Ho_Chi_Minh                   |
| fetch  | fetch                                     | 2 (tự chạy, có audit)     | chặn IP nội bộ / metadata cloud, tôn trọng robots  |
| git    | status, diff*, log, show, branch          | 1 — chỉ đọc               | commit / add / reset / create_branch / checkout TẮT|

Đăng ký đi qua cổng MCP có sẵn (`mcp_gateway`): bật `agent_gateway.allow_stdio`, lưu server, khám phá công cụ, đặt quy tắc từng công
cụ. Mọi bước vào audit (actor `setup_mcp_servers`). Kết quả của mọi công cụ MCP mang nhãn dữ liệu không tin cậy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "mcp-servers"
CONSTRAINTS = VENDOR / "constraints.txt"
PACKAGES = ("time", "fetch", "git")
ACTOR = "setup_mcp_servers"
DEFAULT_TZ = "Asia/Ho_Chi_Minh"

#: Công cụ ghi của server git: tắt (và nếu admin bật lại thì rủi ro 4 = cần duyệt).
GIT_WRITE_TOOLS = ("git_commit", "git_add", "git_reset", "git_create_branch", "git_checkout")


def venv_dir() -> Path:
    env = os.environ.get("VNMATEAI_MCP_VENV")
    if env:
        return Path(env)
    if Path("/opt/mcp-venv").is_dir() and not (ROOT / ".mcp-venv").is_dir():
        return Path("/opt/mcp-venv")                     # image Docker
    return ROOT / ".mcp-venv"


def venv_python(base: Optional[Path] = None) -> Path:
    base = base or venv_dir()
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install(base: Optional[Path] = None) -> Path:
    """Tạo venv riêng (nếu chưa có) và cài ba server từ mã đã đóng gói, theo phiên bản ghim."""
    base = base or venv_dir()
    py = venv_python(base)
    if not py.exists():
        print(f"[mcp] Tạo môi trường riêng {base} …")
        subprocess.run([sys.executable, "-m", "venv", str(base)], check=True)
    cmd = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check"]
    if CONSTRAINTS.exists():
        cmd += ["-c", str(CONSTRAINTS)]
    cmd += [str(VENDOR / p) for p in PACKAGES]
    print("[mcp] Cài server:", ", ".join(PACKAGES))
    subprocess.run(cmd, check=True)
    return py


def specs(py: Path, *, git_repo: Optional[Path], tz: str = DEFAULT_TZ) -> Dict[str, Dict[str, Any]]:
    """Khai báo từng server cho cổng MCP + quy tắc từng công cụ (`overrides`: tên → {enabled, risk})."""
    out: Dict[str, Dict[str, Any]] = {
        "time": {"payload": {"name": "Giờ & múi giờ (MCP chính thức)", "transport": "stdio", "command": str(py),
                             "args": ["-m", "mcp_server_time", "--local-timezone", tz], "default_risk": 1, "timeout_s": 20,
                             "data_classification": "PUBLIC",
                             "description": "Giờ hiện tại theo múi giờ IANA và đổi giờ giữa các múi (modelcontextprotocol/servers)."},
                 "overrides": {}},
        "fetch": {"payload": {"name": "Đọc trang web (MCP chính thức)", "transport": "stdio", "command": str(py),
                              "args": ["-m", "mcp_server_fetch"], "default_risk": 2, "timeout_s": 45, "data_classification": "PUBLIC",
                              "description": ("Tải một trang web công khai và chuyển thành Markdown. Chặn địa chỉ nội bộ / loopback / metadata "
                                              "cloud ở mọi bước chuyển hướng; tôn trọng robots.txt. Nội dung trang là dữ liệu không tin cậy.")},
                  # Gợi ý của server là chỉ đọc (rủi ro 1) — đặt 2: URL do AI soạn có thể mang dữ liệu ra ngoài; vẫn tự chạy, có audit.
                  "overrides": {"fetch": {"risk": 2}}},
    }
    if git_repo is not None:
        out["git"] = {"payload": {"name": "Git — chỉ đọc (MCP chính thức)", "transport": "stdio", "command": str(py),
                                  "args": ["-m", "mcp_server_git", "--repository", str(git_repo)], "default_risk": 3, "timeout_s": 30,
                                  "data_classification": "INTERNAL",
                                  "description": (f"Xem trạng thái, diff, lịch sử, nhánh của kho {git_repo.name} (giới hạn trong kho này). "
                                                  "Công cụ ghi (commit, add, reset, tạo nhánh, checkout) bị tắt.")},
                      "overrides": {t: {"enabled": False, "risk": 4} for t in GIT_WRITE_TOOLS}}
    return out


def _enable_stdio(dry: bool) -> None:
    from mateai.config.loader import reload_settings, settings, update_config_section
    if settings.agent_gateway.allow_stdio:
        return
    print("[mcp] Bật agent_gateway.allow_stdio (máy chủ chạy các server cục bộ đã rà soát ở vendor/mcp-servers)")
    if not dry:
        update_config_section("agent_gateway", {"allow_stdio": True})
        reload_settings()


async def register(all_specs: Dict[str, Dict[str, Any]], *, dry: bool = False) -> List[Dict[str, Any]]:
    from mateai.application.agent_gateway import mcp_gateway as gw
    _enable_stdio(dry)
    results = []
    for sid, sp in all_specs.items():
        if dry:
            results.append({"server": sid, "dry_run": True, "payload": sp["payload"], "overrides": sp["overrides"]})
            continue
        gw.save_server(sid, {**sp["payload"], "enabled": True}, ACTOR)
        disc = await gw.discover(sid, ACTOR)
        for tool, ov in sp["overrides"].items():
            try:
                gw.set_tool_override(sid, tool, ov.get("enabled"), ov.get("risk"), ACTOR)
            except gw.GatewayError as exc:
                disc.setdefault("warnings", []).append(f"{tool}: {exc}")
        rec = gw.get_server(sid) or {}
        tools = [{"name": t["name"], "registered_as": t["registered_as"], "risk": t["risk"], "enabled": t["enabled"]}
                 for t in (rec.get("tools") or [])]
        results.append({**disc, "tools_detail": tools})
    return results


async def close_clients() -> None:
    """Đóng các tiến trình server đã mở để khám phá công cụ (máy chủ VN-MateAI tự mở lại khi cần)."""
    from mateai.application.agent_gateway import mcp_gateway as gw
    for sid in list(gw._clients):
        client = gw._clients.pop(sid, None)
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass


async def _run(all_specs: Dict[str, Dict[str, Any]], dry: bool) -> List[Dict[str, Any]]:
    try:
        return await register(all_specs, dry=dry)
    finally:
        await close_clients()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true", help="tạo môi trường riêng và cài / cập nhật server trước khi đăng ký")
    ap.add_argument("--git-repo", type=Path, default=None, help="kho Git cho server git (mặc định: thư mục dự án nếu là kho Git)")
    ap.add_argument("--no-git", action="store_true", help="không đăng ký server git")
    ap.add_argument("--timezone", default=DEFAULT_TZ)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    py = install() if a.install and not a.dry_run else venv_python()
    if not py.exists() and not a.dry_run:
        print(f"[mcp] Chưa có môi trường {venv_dir()} — chạy lại với --install", file=sys.stderr)
        return 2
    repo = None if a.no_git else (a.git_repo or ROOT)
    if repo is not None and not (Path(repo) / ".git").exists():
        print(f"[mcp] {repo} không phải kho Git — bỏ server git")
        repo = None
    results = asyncio.run(_run(specs(py, git_repo=Path(repo) if repo else None, tz=a.timezone), a.dry_run))
    bad = 0
    for r in results:
        if r.get("dry_run"):
            print(json.dumps(r, ensure_ascii=False, indent=1))
            continue
        state = "OK" if r["ok"] else f"LỖI: {r['error']}"
        print(f"[mcp] {r['server']}: {state} — {r['registered']}/{r['tools']} công cụ đăng ký")
        for t in r["tools_detail"]:
            print(f"        {'bật' if t['enabled'] else 'TẮT'}  rủi ro {t['risk']}  {t['registered_as']}")
        for w in r.get("warnings", []):
            print(f"        cảnh báo: {w}")
        bad += 0 if r["ok"] else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
