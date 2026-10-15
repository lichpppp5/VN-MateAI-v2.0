# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Bản quyền ngoại tuyến + đo lượng sử dụng (thẩm định thương mại #23): chữ ký Ed25519 của nhà cung cấp, sửa tệp là mất hiệu lực,
hết hạn / thiếu → hạn mức dùng thử, bản nội bộ không giới hạn, không bao giờ khoá đăng nhập — chỉ chặn tạo thêm.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from mateai.application import licensing
from mateai.infrastructure.security import signing

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def keys(tmp_path, monkeypatch):
    """Cặp khoá thử riêng — không đụng khoá thật của nhà cung cấp."""
    priv, pub = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate(priv, pub)
    monkeypatch.setenv("VNMATEAI_VENDOR_PUBKEY", str(pub))
    monkeypatch.setattr(signing, "load_private_key", lambda path=None, _p=priv: signing.serialization.load_pem_private_key(_p.read_bytes(), None))
    monkeypatch.setattr(licensing, "license_path", lambda: tmp_path / "license.json")
    return tmp_path


def _issue(**kw):
    spec = importlib.util.spec_from_file_location("issue_license", ROOT / "scripts" / "issue_license.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    args = {"customer": "Công ty A", "users": 3, "workstations": 2, "expires": "2099-12-31"}
    args.update(kw)
    return mod.issue(**args)


def test_valid_license_and_tamper_detection(keys):
    doc = _issue()
    assert licensing.check(doc)["state"] == "valid"
    bad = copy.deepcopy(doc)
    bad["license"]["max_users"] = 9999
    assert licensing.check(bad)["state"] == "invalid"
    assert licensing.check({"license": doc["license"], "signature": "khong-phai-base64"})["state"] == "invalid"
    assert licensing.check(_issue(expires="2000-01-01"), today=date(2026, 1, 1))["state"] == "expired"


def test_internal_edition_unlimited(keys, monkeypatch):
    monkeypatch.delenv("VNMATEAI_EDITION", raising=False)
    assert licensing.status()["state"] == "internal"
    licensing.ensure_can_add("users")                                   # không ném


def test_commercial_limits_from_license_then_trial(keys, monkeypatch):
    monkeypatch.setenv("VNMATEAI_EDITION", "commercial")
    assert licensing.status()["limits"] == licensing.TRIAL               # chưa có tệp → dùng thử
    st = licensing.install(_issue(users=7, workstations=3))
    assert st["state"] == "valid" and st["limits"] == {"max_users": 7, "max_workstations": 3}
    monkeypatch.setattr(licensing, "usage", lambda: {"users": 7, "workstations": 1})
    with pytest.raises(ValueError, match="hạn mức 7 người dùng"):
        licensing.ensure_can_add("users")
    licensing.ensure_can_add("workstations")


def test_install_rejects_forged_file(keys):
    doc = _issue()
    doc["license"]["customer"] = "Công ty khác"
    with pytest.raises(ValueError, match="chữ ký"):
        licensing.install(doc)
    assert not (keys / "license.json").exists()


def test_usage_counts_real_tables():
    u = licensing.usage()
    assert {"users", "workstations", "voice_turns_month", "llm_tokens_month", "state"} <= set(u)
    assert u["users"] >= 1


def test_vendor_public_key_ships_and_private_key_does_not():
    assert (ROOT / "deploy" / "keys" / "vendor_signing.pub").is_file()
    import subprocess
    out = subprocess.run(["git", "check-ignore", "-q", "certs/vendor_signing.key"], cwd=ROOT)
    assert out.returncode == 0, "khoá riêng phải bị .gitignore"
