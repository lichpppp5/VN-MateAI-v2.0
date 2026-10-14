# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Dev Fleet: dịch vụ điều phối chạy với Ubuntu Master GIẢ theo hợp đồng v1 (tests/fake_master.py).
Kiểm: chế độ tắt/chỉ đọc, ảnh chụp tươi/cũ, dry-run, giao việc qua cổng chính sách (chờ duyệt / bị chặn / giao trùng),
kiểm chứng bằng bằng chứng, mất liên lạc Master, thuê workspace, huỷ, chạy lại, treo, tiến độ.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_master import FakeMaster  # noqa: E402

from mateai.application.devfleet import models as m  # noqa: E402
from mateai.application.devfleet.provider import FleetError  # noqa: E402
from mateai.application.devfleet.service import DevFleetService, FleetModeError  # noqa: E402
from mateai.config.loader import DevFleetConfig  # noqa: E402
from mateai.infrastructure.database.db_manager import db_manager  # noqa: E402


@pytest.fixture(scope="module")
def master():
    with FakeMaster() as srv:
        yield srv


def make(master, **over):
    cfg = DevFleetConfig(enabled=True, mode="controlled", endpoint=master.url, api_token=master.token, cache_ttl_s=0.0,
                         timeout_s=2.0, **over)
    return DevFleetService(config=lambda: cfg)


def task_payload(**kw):
    base = {"title": "Sửa reconnect websocket", "objective": "Sửa lỗi websocket không tự nối lại sau khi mất mạng",
            "acceptance_criteria": ["test_reconnect pass"], "verification_steps": ["pytest tests/test_ws.py"],
            "risk": "low", "require_tests": True, "required_capabilities": ["git", "python"]}
    base.update(kw)
    return base


async def create(svc, **kw):
    key = kw.pop("idempotency_key", None)
    return await svc.create_task(task_payload(**kw), requested_by="tester", check_rbac=False, idempotency_key=key)


@pytest.fixture(autouse=True)
def _reset(master):
    with db_manager._get_connection() as conn:                                  # mỗi test một sổ Dev Fleet sạch
        for table in ("dev_runs", "dev_leases", "dev_events", "dev_projects"):
            conn.execute(f"DELETE FROM {table};")
        conn.commit()
    master.tasks.clear()
    master.idempotent.clear()
    master.dispatch_count = 0
    master.git_head = ""
    master.calls.clear()
    master.api_version = "1.0"
    master._fail = 0
    yield


# ── chế độ ──────────────────────────────────────────────────────────────────

async def test_disabled_module_does_no_io_and_other_features_unaffected():
    svc = DevFleetService(config=lambda: DevFleetConfig(enabled=False, endpoint="http://127.0.0.1:1"))
    st = await svc.status()
    assert st["enabled"] is False and st["mode"] == "disabled" and st["master"] is None
    with pytest.raises(FleetModeError) as exc:
        await svc.workers()
    assert exc.value.code == "disabled"


async def test_unconfigured_endpoint_is_reported_not_crashed():
    svc = DevFleetService(config=lambda: DevFleetConfig(enabled=True, mode="read_only"))
    st = await svc.status()
    assert st["configured"] is False and "endpoint" in st["error"]


async def test_read_only_mode_reads_but_never_controls(master):
    svc = make(master, mode="read_only") if False else DevFleetService(config=lambda: DevFleetConfig(
        enabled=True, mode="read_only", endpoint=master.url, api_token=master.token, cache_ttl_s=0.0))
    assert len(await svc.workers()) == 3
    with pytest.raises(FleetModeError) as exc:
        await svc.create_task(task_payload(), requested_by="t", check_rbac=False)
    assert exc.value.code == "read_only"
    assert master.dispatch_count == 0


# ── đọc hạ tầng ─────────────────────────────────────────────────────────────

async def test_snapshot_normalises_master_workers_and_agents(master):
    svc = make(master)
    snap = await svc.snapshot(force=True)
    assert snap["reachable"] and snap["master"]["health"] == "HEALTHY" and snap["master"]["name"] == "Ubuntu-Master"
    by = {w["worker_id"]: w for w in snap["workers"]}
    assert by["mac-01"]["state"] == m.IDLE and by["mac-01"]["capabilities"] == ["docker", "git", "nodejs", "python"]
    assert by["mac-03"]["state"] == m.OFFLINE                       # last_seen năm 2020 -> không báo ONLINE
    assert snap["agents"][0]["role"] == "Backend Developer"
    assert master.token not in str(snap)


async def test_status_counts_come_from_real_data(master):
    st = await make(master).status()
    assert st["workers"]["total"] == 3 and st["workers"]["by_state"] == {"IDLE": 2, "OFFLINE": 1}
    assert st["tasks"]["total"] == 0 or isinstance(st["tasks"]["total"], int)


async def test_bad_token_reports_rejected_without_leaking_it(master):
    cfg = DevFleetConfig(enabled=True, mode="controlled", endpoint=master.url, api_token="SAI-TOKEN", cache_ttl_s=0.0, timeout_s=2.0)
    snap = await DevFleetService(config=lambda: cfg).snapshot(force=True)
    assert snap["reachable"] is False and "xác thực" in snap["error"] and "SAI-TOKEN" not in str(snap)


async def test_incompatible_api_version_is_refused(master):
    master.api_version = "2.0"
    snap = await make(master).snapshot(force=True)
    assert not snap["reachable"] and snap["error_kind"] == "incompatible"


async def test_master_down_demotes_workers_to_unknown_instead_of_faking_online(master):
    svc = make(master)
    assert (await svc.snapshot(force=True))["workers"][0]["state"] == m.IDLE
    master.fail_next(20)
    snap = await svc.snapshot(force=True)
    assert not snap["reachable"] and snap["master"]["health"] == "OFFLINE"
    assert all(w["state"] in (m.UNKNOWN, m.DISABLED) for w in snap["workers"])
    master._fail = 0


async def test_plain_http_to_a_public_host_is_refused():
    from mateai.infrastructure.connectors.dev_fleet_master import DevFleetMasterClient
    with pytest.raises(FleetError) as exc:
        await DevFleetMasterClient("http://master.example.com", "t").health()
    assert exc.value.kind == "rejected"


async def test_unsafe_ids_never_reach_the_master(master):
    from mateai.infrastructure.connectors.dev_fleet_master import DevFleetMasterClient
    client = DevFleetMasterClient(master.url, master.token)
    for bad in ("../x", "a/b", "a b", ""):
        with pytest.raises(FleetError):
            await client.get_worker(bad)
    assert master.calls == []


# ── lập kế hoạch / giao việc ────────────────────────────────────────────────

async def test_dry_run_chooses_a_worker_and_changes_nothing(master):
    svc = make(master)
    plan = await svc.plan(task_payload(required_capabilities=["xcode"]))
    assert plan["ok"] and plan["worker_id"] == "mac-02" and plan["requires_approval"] is False
    assert any(r["worker_id"] == "mac-01" and "xcode" in r["reason"] for r in plan["rejected"])
    assert master.dispatch_count == 0 and svc.tasks() == [] or all(t["title"] != "Sửa reconnect websocket" for t in svc.tasks())


async def test_no_eligible_worker_creates_nothing(master):
    out = await create(make(master), required_capabilities=["android"])
    assert out["status"] == "no_worker" and master.dispatch_count == 0


async def test_low_risk_task_is_dispatched_once_with_idempotency_key(master):
    svc = make(master)
    out = await create(svc, idempotency_key="khoa-giao-1")
    assert out["status"] == "dispatched" and out["worker_id"] == "mac-01"
    assert master.dispatch_count == 1
    dispatch_call = [c for c in master.calls if c["path"].endswith("/tasks/dispatch")][0]
    assert dispatch_call["idem"] == "khoa-giao-1"
    run = db_manager.dev_get("dev_runs", out["run_id"])
    assert run["status"] == m.RUN_QUEUED and run["worker_id"] == "mac-01"
    task = svc.task(out["task_id"])
    assert task["status"] == "EXECUTING" and task["kind"] == "dev_task"
    again = await create(svc, idempotency_key="khoa-giao-1")                   # client gửi lại
    assert again["status"] == "duplicate" and again["task_id"] == out["task_id"] and master.dispatch_count == 1


async def test_medium_risk_waits_for_approval_and_master_is_not_called(master):
    svc = make(master)
    out = await create(svc, risk="medium", rollback="git revert")
    assert out["status"] == "awaiting_approval" and out["approval_id"]
    assert master.dispatch_count == 0
    task = svc.task(out["task_id"])
    assert task["status"] == "WAITING_AUTHORIZATION" and task["display_status"] == "WAITING_APPROVAL"
    from mateai.application.security.zero_trust import hitl_manager
    result = await hitl_manager.approve_async(out["approval_id"], approved_by="boss")
    assert result.get("executed") is True, result
    assert master.dispatch_count == 1
    assert svc.task(out["task_id"])["status"] == "EXECUTING"


async def test_kill_switch_blocks_ai_dispatch_outside_the_llm(master, monkeypatch):
    from mateai.config import loader
    monkeypatch.setattr(loader.settings.autonomy, "kill_switch", True)
    out = await make(master).create_task(task_payload(), requested_by="ai", agent_id="VN-MATEAI-CONNECTOR", check_rbac=False)
    assert out["status"] == "denied" and master.dispatch_count == 0


async def test_dispatch_rechecks_worker_after_approval(master):
    svc = make(master)
    out = await create(svc, risk="medium", rollback="git revert")
    master.workers[0]["state"] = "offline"
    master.workers[0]["last_seen"] = "2020-01-01T00:00:00Z"
    try:
        from mateai.application.security.zero_trust import hitl_manager
        res = await hitl_manager.approve_async(out["approval_id"], approved_by="boss")
        assert master.dispatch_count == 0
        assert svc.task(out["task_id"])["status"] == "BLOCKED", res
    finally:
        master.workers[0]["state"] = "idle"
        from mateai.application.devfleet.models import parse_time  # noqa: F401
        import datetime as dt
        master.workers[0]["last_seen"] = dt.datetime.now(dt.timezone.utc).isoformat()


# ── theo dõi + kiểm chứng ───────────────────────────────────────────────────

async def test_master_finished_but_tests_failed_is_not_completed(master):
    svc = make(master)
    out = await create(svc)
    master.set_task(out["run_id"], status="FINISHED", exit_code=0, test_status="FAIL", summary="đã sửa")
    sync = await svc.sync_run(out["run_id"])
    assert sync["run_status"] == m.RUN_FINISHED
    task = svc.task(out["task_id"])
    assert task["status"] == "VERIFYING" and task["verification_status"] == "failed"
    assert any("FAIL" in e["summary"] for e in task["evidence"])


async def test_verified_completion_requires_passing_evidence(master):
    svc = make(master)
    out = await create(svc)
    master.set_task(out["run_id"], status="FINISHED", exit_code=0, test_status="PASS", summary="đã sửa reconnect")
    await svc.sync_run(out["run_id"])
    task = svc.task(out["task_id"])
    assert task["status"] == "COMPLETED" and task["verification_status"] == "passed"
    assert db_manager.dev_leases() == [] or all(l["owner_task_id"] != out["task_id"] for l in db_manager.dev_leases())


async def test_agent_claim_without_independent_evidence_is_completed_unverified(master):
    svc = make(master)
    out = await create(svc, require_tests=False)
    master.set_task(out["run_id"], status="FINISHED", exit_code=0, summary="xong")
    await svc.sync_run(out["run_id"])
    task = svc.task(out["task_id"])
    assert task["status"] == "VERIFYING" and task["display_status"] == "COMPLETED_UNVERIFIED"
    progress = svc.project_progress("khong-co")
    assert progress["percent"] is None                                          # không bịa 0%


async def test_commit_is_verified_against_masters_git_state(master):
    svc = make(master)
    out = await create(svc, require_tests=False, require_commit=True, repository="vn-mateai", branch="fix/ws")
    master.set_task(out["run_id"], status="FINISHED", exit_code=0, git_commit="abc1234", changed_files=["ws.py"])
    master.git_head = "abc1234ffff"
    await svc.sync_run(out["run_id"])
    assert svc.task(out["task_id"])["status"] == "COMPLETED"


async def test_lost_master_marks_run_interrupted_never_completed(master):
    svc = make(master)
    out = await create(svc)
    master.fail_next(20)
    res = await svc.sync_run(out["run_id"])
    master._fail = 0
    assert res["run_status"] == m.RUN_INTERRUPTED
    task = svc.task(out["task_id"])
    assert task["status"] == "EXECUTING" and task["display_status"] == "INTERRUPTED_UNKNOWN"
    assert svc.stuck()[0]["run_id"] == out["run_id"]
    master.set_task(out["run_id"], status="RUNNING")                              # Master quay lại, việc vẫn chạy
    assert (await svc.sync_run(out["run_id"]))["run_status"] == m.RUN_RUNNING


async def test_unknown_to_master_is_not_assumed_finished(master):
    svc = make(master)
    out = await create(svc)
    master.tasks.clear()                                                         # Master "quên" lượt chạy
    res = await svc.sync_run(out["run_id"])
    assert res["run_status"] == m.RUN_UNKNOWN and svc.task(out["task_id"])["status"] == "EXECUTING"


async def test_progress_updates_only_when_something_changed(master):
    svc = make(master)
    out = await create(svc)
    master.set_task(out["run_id"], status="RUNNING", progress=10)
    await svc.sync_run(out["run_id"])
    first = db_manager.dev_get("dev_runs", out["run_id"])["last_progress_at"]
    db_manager.dev_update("dev_runs", out["run_id"], {"last_progress_at": "2020-01-01 00:00:00"})
    await svc.sync_run(out["run_id"])                                            # không đổi gì
    assert db_manager.dev_get("dev_runs", out["run_id"])["last_progress_at"] == "2020-01-01 00:00:00"
    assert first
    assert any(s["run_id"] == out["run_id"] and "tiến triển" in " ".join(s["reasons"]) for s in svc.stuck())
    master.set_task(out["run_id"], progress=40)
    await svc.sync_run(out["run_id"])
    assert db_manager.dev_get("dev_runs", out["run_id"])["last_progress_at"] != "2020-01-01 00:00:00"


# ── thuê workspace / huỷ / chạy lại ─────────────────────────────────────────

async def test_same_branch_cannot_be_worked_on_twice(master):
    svc = make(master)
    a = await create(svc, repository="vn-mateai", branch="fix/ws")
    b = await create(svc, repository="vn-mateai", branch="fix/ws")
    assert a["status"] == "dispatched"
    assert b["status"] in ("blocked", "no_worker") and master.dispatch_count == 1
    await svc.cancel_task(a["task_id"], requested_by="tester", check_rbac=False)
    c = await create(svc, repository="vn-mateai", branch="fix/ws")
    assert c["status"] == "dispatched"


async def test_cancel_goes_through_master_and_releases_everything(master):
    svc = make(master)
    out = await create(svc, repository="vn-mateai", branch="x")
    res = await svc.cancel_task(out["task_id"], requested_by="tester", check_rbac=False)
    assert res["status"] == "cancelled"
    assert master.tasks[out["run_id"]]["status"] == "CANCELLED"
    assert svc.task(out["task_id"])["status"] == "CANCELLED"
    assert all(l["owner_task_id"] != out["task_id"] for l in db_manager.dev_leases())
    assert (await svc.cancel_task(out["task_id"], requested_by="tester", check_rbac=False))["status"] == "noop"


async def test_retry_after_failed_verification_uses_a_new_attempt_and_respects_the_limit(master):
    svc = make(master)
    out = await create(svc, max_retries=1)
    master.set_task(out["run_id"], status="FINISHED", exit_code=0, test_status="FAIL")
    await svc.sync_run(out["run_id"])
    retry = await svc.retry_task(out["task_id"], requested_by="tester", check_rbac=False)
    assert retry["status"] == "dispatched" and retry["run_id"] != out["run_id"]
    assert [r["attempt"] for r in svc.task(out["task_id"])["runs"]] == [1, 2]
    assert master.dispatch_count == 2
    master.set_task(retry["run_id"], status="FINISHED", exit_code=0, test_status="FAIL")
    await svc.sync_run(retry["run_id"])
    assert svc.task(out["task_id"])["status"] == "FAILED"                        # hết số lần thử lại


async def test_retry_refuses_when_old_run_cannot_be_confirmed_stopped(master):
    svc = make(master)
    out = await create(svc)
    master.fail_next(20)
    res = await svc.retry_task(out["task_id"], requested_by="tester", check_rbac=False)
    master._fail = 0
    assert res["status"] == "refused" and master.dispatch_count == 1


async def test_reassign_to_another_worker(master):
    svc = make(master)
    out = await create(svc)
    assert out["worker_id"] == "mac-01"
    res = await svc.retry_task(out["task_id"], requested_by="tester", worker_id="mac-02", check_rbac=False)
    assert res["status"] == "dispatched" and res["worker_id"] == "mac-02"
    assert master.tasks[out["run_id"]]["status"] == "CANCELLED"


# ── dự án / báo cáo / kill switch ───────────────────────────────────────────

async def test_project_progress_is_weighted_and_counts_only_verified_work(master):
    svc = make(master)
    prj = svc.create_project("VN-MateAI", repository="vn-mateai", preferred_workers=["mac-02"], created_by="t")
    a = await create(svc, project_id=prj["project_id"], priority="high")
    b = await create(svc, project_id=prj["project_id"], priority="low")
    assert svc.project_progress(prj["project_id"])["percent"] == 0.0
    master.set_task(a["run_id"], status="FINISHED", exit_code=0, test_status="PASS")
    await svc.sync_run(a["run_id"])
    p = svc.project_progress(prj["project_id"])
    assert p["tasks"] == 2 and p["percent"] == 75.0                              # high=3, low=1
    assert (await svc.plan(task_payload(project_id=prj["project_id"])))["worker_id"] == "mac-02"     # ưa máy của dự án
    assert b["status"] == "dispatched"


async def test_disabled_worker_is_never_scheduled(master):
    svc = make(master, disabled_workers=["mac-01"])
    plan = await svc.plan(task_payload())
    assert plan["worker_id"] == "mac-02"
    assert any(r["worker_id"] == "mac-01" and "tắt" in r["reason"] for r in plan["rejected"])


async def test_briefing_reports_only_real_facts(master):
    svc = make(master)
    out = await create(svc)
    db_manager.dev_update("dev_runs", out["run_id"], {"last_progress_at": "2020-01-01 00:00:00"})
    brief = await svc.briefing()
    assert brief["status"]["workers"]["total"] == 3 and "mac-03" in brief["offline_workers"]
    assert any(out["run_id"] in line for line in brief["recommended"])
    assert svc.events(5)


def test_dev_fleet_package_keeps_its_boundaries():
    """Kiến trúc: service không biết HTTP / SSH / Ansible / OpenClaw và không đụng nội bộ âm thanh."""
    import ast
    root = Path(__file__).resolve().parents[1] / "src" / "mateai" / "application" / "devfleet"
    banned = {"httpx", "requests", "aiohttp", "paramiko", "asyncssh", "subprocess", "sqlite3", "openai", "websockets"}
    for py in root.glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert name.split(".")[0] not in banned, f"{py.name} import {name}"
                assert "audio" not in name and "llm" not in name.lower().split(".")[-1], f"{py.name} import {name}"
