# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""Dev Fleet: phần THUẦN (không I/O) — chuẩn hoá dữ liệu Master, đặc tả tác vụ, chọn máy, kiểm chứng."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mateai.application.devfleet import models as m
from mateai.application.devfleet.scheduler import rank_workers
from mateai.application.devfleet.verification import FAILED, INCONCLUSIVE, PASSED, verify_result

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def worker(**kw):
    raw = {"worker_id": "mac-1", "state": "idle", "last_seen": NOW.isoformat(), "capabilities": ["git", "Python"]}
    raw.update(kw)
    return m.normalise_worker(raw, now=NOW, stale_after_s=60, offline_after_s=180)


def spec(**kw):
    base = {"title": "Sửa reconnect", "objective": "Sửa lỗi websocket không tự nối lại", "acceptance_criteria": ["test pass"],
            "verification_steps": ["pytest"], "risk": "low"}
    base.update(kw)
    return m.parse_task_spec(base)


# ── chuẩn hoá worker ────────────────────────────────────────────────────────

def test_fresh_idle_worker_keeps_state_and_unknown_metrics_stay_none():
    w = worker()
    assert w["state"] == m.IDLE and w["freshness"] == m.FRESH and w["capabilities"] == ["git", "python"]
    assert w["cpu_percent"] is None and w["memory_percent"] is None and w["disk_percent"] is None      # không bịa 0


def test_worker_not_seen_for_long_is_offline_even_if_master_says_busy():
    w = worker(state="busy", last_seen=(NOW - timedelta(seconds=500)).isoformat())
    assert w["state"] == m.OFFLINE and w["declared_state"] == m.BUSY


def test_stale_worker_is_flagged_not_silently_online():
    w = worker(last_seen=(NOW - timedelta(seconds=100)).isoformat())
    assert w["freshness"] == m.STALE and w["state"] == m.IDLE


def test_unknown_words_and_missing_ids():
    assert worker(state="???")["state"] == m.UNKNOWN
    assert m.normalise_worker({"hostname": "x"}, now=NOW, stale_after_s=60, offline_after_s=180) is None


def test_manually_disabled_worker_is_disabled():
    w = m.normalise_worker({"worker_id": "mac-1", "state": "idle", "last_seen": NOW.isoformat()}, now=NOW,
                           stale_after_s=60, offline_after_s=180, disabled=["mac-1"])
    assert w["state"] == m.DISABLED


@pytest.mark.parametrize("value,expected", [(1_700_000_000, True), (1_700_000_000_000, True), ("2026-10-07T12:00:00Z", True),
                                            ("không phải ngày", False), (None, False)])
def test_parse_time_accepts_epoch_ms_and_iso(value, expected):
    assert (m.parse_time(value) is not None) is expected


# ── đặc tả tác vụ ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("patch,why", [
    ({"objective": "sửa"}, "objective"), ({"acceptance_criteria": []}, "acceptance_criteria"),
    ({"verification_steps": None}, "verification_steps"), ({"risk": "vừa"}, "risk"),
    ({"risk": "high", "rollback": ""}, "rollback"), ({"require_commit": True}, "repository"),
    ({"repository": "a b; rm -rf /"}, "không an toàn"), ({"deadline": "mai"}, "deadline"),
])
def test_vague_or_unsafe_specs_are_refused(patch, why):
    with pytest.raises(m.SpecError) as exc:
        spec(**patch)
    assert why in str(exc.value)


def test_spec_normalises_capabilities_and_risk_level():
    s = spec(required_capabilities=["Node.js", "GIT", ""], risk="high", rollback="git revert")
    assert s.required_capabilities == ["git", "node.js"] and s.risk_level == 4


# ── chọn máy ────────────────────────────────────────────────────────────────

def test_scheduler_rejects_with_reasons_and_prefers_idle_low_load():
    ws = [worker(worker_id="a", cpu=80), worker(worker_id="b", cpu=10), worker(worker_id="c", state="offline"),
          worker(worker_id="d", capabilities=["git"]), worker(worker_id="e", state="busy")]
    ranked = rank_workers(spec(required_capabilities=["python"]), ws)
    assert [c["worker_id"] for c in ranked["candidates"]] == ["b", "a"]
    reasons = {r["worker_id"]: r["reason"] for r in ranked["rejected"]}
    assert "OFFLINE" in reasons["c"] and "python" in reasons["d"] and "BUSY" in reasons["e"]


def test_scheduler_honours_platform_affinity_lock_and_stale():
    ws = [worker(worker_id="a", platform="macos"), worker(worker_id="b", platform="linux"),
          worker(worker_id="s", last_seen=(NOW - timedelta(seconds=100)).isoformat())]
    ranked = rank_workers(spec(required_platform="macos"), ws, preferred=["a"], locked_workers=[])
    assert [c["worker_id"] for c in ranked["candidates"]] == ["a"]
    assert any("STALE" in r["reason"] for r in ranked["rejected"])
    locked = rank_workers(spec(), [worker(worker_id="a")], locked_workers=["a"])
    assert locked["candidates"] == [] and "thuê" in locked["rejected"][0]["reason"]


def test_scheduler_is_deterministic_and_affinity_beats_equal_load():
    ws = [worker(worker_id="b"), worker(worker_id="a")]
    assert rank_workers(spec(), ws)["candidates"][0]["worker_id"] == "a"
    assert rank_workers(spec(), ws, preferred=["b"])["candidates"][0]["worker_id"] == "b"


def test_scheduler_pins_the_worker_the_human_chose_but_still_checks_capabilities():
    ws = [worker(worker_id="a"), worker(worker_id="b", capabilities=["git"])]
    ranked = rank_workers(spec(worker_id="b", required_capabilities=["python"]), ws)
    assert ranked["candidates"] == []


# ── kiểm chứng ──────────────────────────────────────────────────────────────

def finished(**kw):
    return {"status": "FINISHED", "exit_code": 0, "build_status": "PASS", "test_status": "PASS", **kw}


def test_agent_claim_alone_is_never_enough():
    assert verify_result(spec(), finished())["status"] == INCONCLUSIVE        # không yêu cầu bằng chứng độc lập nào


def test_build_and_tests_pass_gives_passed():
    assert verify_result(spec(require_build=True, require_tests=True), finished())["status"] == PASSED


def test_failing_or_missing_evidence():
    assert verify_result(spec(require_tests=True), finished(test_status="FAIL"))["status"] == FAILED
    assert verify_result(spec(require_tests=True), {"status": "FINISHED", "exit_code": 0})["status"] == INCONCLUSIVE
    assert verify_result(spec(require_tests=True), {"status": "FAILED", "exit_code": 2})["status"] == FAILED
    assert verify_result(spec(require_tests=True), finished(exit_code=1))["status"] == FAILED


def test_commit_is_checked_against_independent_git_state():
    s = spec(require_commit=True, repository="vn-mateai")
    res = finished(git_commit="abc1234", changed_files=["a.py"])
    assert verify_result(s, res, {"head": "abc1234def"})["status"] == PASSED
    assert verify_result(s, res, {"head": "ffff999"})["status"] == FAILED
    assert verify_result(s, res, None)["status"] == INCONCLUSIVE
    assert verify_result(s, finished(), {"head": "abc"})["status"] == FAILED          # không báo commit
