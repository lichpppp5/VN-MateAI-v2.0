# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Nâng cấp bản Docker: giải nén gói mới đè lên rồi chạy install.sh — `.env` của bản trước còn ghi VNMATE_VERSION cũ, nên trước
đây compose vẫn chạy image CŨ. Nay install.sh / install.ps1 đồng bộ phiên bản theo env.example của gói; build_release ghi đúng
phiên bản của gói vào env.example."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "deploy" / "docker"


def _bash():
    """bash dùng được thật: trên Windows `bash` trong PATH có thể là WSL chưa cài distro — ưu tiên Git Bash."""
    import os
    cands = [str(Path(p, "Git", "bin", "bash.exe")) for p in ("C:/Program Files", "C:/Program Files (x86)")] if os.name == "nt" else []
    cands.append(shutil.which("bash") or "")
    for c in cands:
        if c and Path(c).exists():
            try:
                if subprocess.run([c, "-c", "true"], capture_output=True, timeout=20).returncode == 0:
                    return c
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


BASH = _bash()


def _env_block() -> str:
    """Đoạn `# 2. .env` của install.sh (tới trước câu hỏi gateway AI) — chạy riêng, không cần Docker."""
    text = (DOCKER / "install.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("# 2. .env")
    end = text.index('if [ -z "$(getv VNMATE_LLM_BASE_URL)" ]')
    return "set -eu\nsay() { printf '%s\\n' \"$*\"; }\n" + text[start:end]


@pytest.mark.skipif(BASH is None, reason="cần bash")
@pytest.mark.parametrize("old_env,expect_msg", [
    ("VNMATE_VERSION=1.0.0\nVNMATE_ADMIN_PASSWORD=giu-nguyen\nVNMATE_PG_PASSWORD=pg\n", "1.0.0 -> 9.9.9"),
    (None, "Đã tạo .env"),
])
def test_install_sh_syncs_the_bundle_version_and_keeps_secrets(tmp_path, old_env, expect_msg):
    (tmp_path / "env.example").write_text("VNMATE_VERSION=9.9.9\nVNMATE_ADMIN_PASSWORD=\nVNMATE_PG_PASSWORD=\n", encoding="utf-8")
    if old_env is not None:
        (tmp_path / ".env").write_text(old_env, encoding="utf-8")
    script = tmp_path / "block.sh"
    script.write_bytes(_env_block().encode("utf-8"))
    out = subprocess.run([BASH, str(script)], cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert re.search(r"(?m)^VNMATE_VERSION=9\.9\.9$", env) and expect_msg in out.stdout
    if old_env is not None:
        assert "VNMATE_ADMIN_PASSWORD=giu-nguyen" in env                  # mật khẩu đã có không bị sinh lại
    assert len(re.findall(r"(?m)^VNMATE_VERSION=", env)) == 1


def test_install_ps1_syncs_the_bundle_version_too():
    ps1 = (DOCKER / "install.ps1").read_text(encoding="utf-8")
    assert re.search(r'\$bundleVer = .*env\.example', ps1) and 'SetV "VNMATE_VERSION" $bundleVer' in ps1
    assert ps1.index("$bundleVer") < ps1.index("WriteAllLines")             # ghi vào .env trước khi khởi động


def test_release_writes_its_version_into_env_example(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_release as br
    (tmp_path / "app" / "deploy" / "docker").mkdir(parents=True)
    (tmp_path / "app" / "deploy" / "docker" / "entrypoint.sh").write_text("#!/bin/sh\r\nexec \"$@\"\r\n", encoding="utf-8")
    br.copy_package_files(tmp_path, "7.7.7")
    assert re.search(r"(?m)^VNMATE_VERSION=7\.7\.7\r?$", (tmp_path / "env.example").read_text(encoding="utf-8"))
    assert b"\r\n" not in (tmp_path / "install.sh").read_bytes()               # script Linux: xuống dòng LF


def test_one_version_everywhere():
    from mateai import __version__
    py = re.search(r'(?m)^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)
    env = re.search(r"(?m)^VNMATE_VERSION=(\S+)", (DOCKER / "env.example").read_text(encoding="utf-8")).group(1)
    compose = re.search(r"vnmateai/app:\$\{VNMATE_VERSION:-([^}]+)\}", (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")).group(1)
    assert __version__ == py == env == compose
