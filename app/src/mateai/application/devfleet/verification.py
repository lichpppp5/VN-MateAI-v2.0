# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/devfleet/verification.py
===========================================
Kiểm chứng kết quả tác vụ Dev bằng BẰNG CHỨNG, không bằng lời agent (prompt §28–§30, §101).

OpenClaw / Master báo "xong" chỉ là *execution finished*. Tác vụ chỉ được COMPLETED khi các điều kiện đặc tả yêu cầu
(build, test, commit) đều có bằng chứng PASS — commit còn được đối chiếu với trạng thái Git do Master báo độc lập.
Thiếu bằng chứng -> `inconclusive` (hiển thị COMPLETED_UNVERIFIED, cần người xác nhận), KHÔNG tự coi là xong.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mateai.application.devfleet.models import TaskSpec

PASSED, FAILED, INCONCLUSIVE = "passed", "failed", "inconclusive"
_PASS = {"PASS", "PASSED", "SUCCESS", "OK", "GREEN"}
_FAIL = {"FAIL", "FAILED", "ERROR", "RED"}


def _gate(value: Any) -> Optional[bool]:
    """PASS -> True · FAIL -> False · thiếu / không rõ -> None (không bịa)."""
    text = str(value or "").strip().upper()
    if text in _PASS:
        return True
    if text in _FAIL:
        return False
    return None


def verify_result(spec: TaskSpec, result: Dict[str, Any], git: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: Optional[bool], detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    status = str(result.get("status") or "").upper()
    exit_code = result.get("exit_code")
    if status in ("FINISHED", "COMPLETED", "SUCCEEDED", "SUCCESS"):
        add("execution", exit_code == 0 if exit_code is not None else None,
            f"exit_code={exit_code}" if exit_code is not None else "Master không báo exit_code")
    elif status in ("FAILED", "ERROR"):
        add("execution", False, f"Master báo thất bại (exit_code={exit_code})")
    else:
        add("execution", None, f"trạng thái thực thi chưa kết thúc: {status or 'không rõ'}")

    independent = 0
    if spec.require_build:
        independent += 1
        ok = _gate(result.get("build_status"))
        add("build", ok, f"build_status={result.get('build_status')!r}")
    if spec.require_tests:
        independent += 1
        ok = _gate(result.get("test_status"))
        add("tests", ok, f"test_status={result.get('test_status')!r}")
    if spec.require_commit:
        independent += 1
        commit = str(result.get("git_commit") or "").strip()
        head = str((git or {}).get("head") or (git or {}).get("commit") or "").strip()
        if not commit:
            add("commit", False, "agent không báo git_commit")
        elif not head:
            add("commit", None, "không có trạng thái Git độc lập từ Master để đối chiếu")
        else:
            same = head.startswith(commit) or commit.startswith(head)
            add("commit", same, f"HEAD trên máy = {head[:12]}, agent báo {commit[:12]}")
        if result.get("changed_files") in (None, []) and commit:
            add("changed_files", None, "agent báo commit nhưng không liệt kê tệp thay đổi")
    if independent == 0:
        add("independent_evidence", None,
            "đặc tả không yêu cầu build / test / commit nào nên không có bằng chứng độc lập ngoài lời agent")

    if any(c["ok"] is False for c in checks):
        verdict = FAILED
    elif any(c["ok"] is None for c in checks):
        verdict = INCONCLUSIVE
    else:
        verdict = PASSED
    return {"status": verdict, "checks": checks}
