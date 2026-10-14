# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
LLMOps: đo mức dùng AI (chỉ số THẬT, không đoán giá), ngân sách (off / warn / block, theo ngày / tháng / người), cổng chặn trong vòng agent,
bộ đánh giá chất lượng (chấm bằng điều kiện dữ liệu, phát hiện ca tụt, chỉ công cụ chỉ đọc) và API phân quyền.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mateai.application.llmops import evaluation as ev
from mateai.application.llmops import usage
from mateai.config.loader import settings
from mateai.infrastructure.database.db_manager import db_manager
from mateai.interfaces.http.auth_dependencies import get_current_user
from mateai.interfaces.http.routers.llmops import router


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for table, key in (("llm_usage", "usage_id"), ("ai_eval_sets", "set_id"), ("ai_eval_runs", "run_id")):
        for r in db_manager.dev_list(table, limit=5000):
            db_manager.dev_delete(table, r[key])
    monkeypatch.setitem(settings.llm.model_registry, "gia-co", {"status": "APPROVED", "price_in_per_1m": 2.0, "price_out_per_1m": 10.0})
    monkeypatch.setattr(settings.llmops, "budget_mode", "warn")
    for f in ("daily_token_limit", "user_daily_token_limit", "daily_cost_limit", "monthly_cost_limit"):
        monkeypatch.setattr(settings.llmops, f, 0)
    usage._alerted.clear()
    yield


# ── mức dùng ────────────────────────────────────────────────────────────────

def test_record_prices_only_known_models_and_never_guesses():
    usage.record("an", "gia-co", 1_000_000, 500_000)
    usage.record("an", "model-la", 1000, 1000)
    usage.record("binh", "gia-co", 0, 0)                      # không có token -> không ghi dòng rỗng
    s = usage.summary(7)
    assert s["total"]["calls"] == 2 and s["total"]["tokens"] == 1_502_000
    assert s["total"]["cost"] == pytest.approx(2.0 + 5.0)       # 1M*2 + 0.5M*10 trên 1 triệu
    assert s["total"]["unpriced_tokens"] == 2000 and "chưa khai giá" in s["note"]
    assert {m["key"]: m["cost"] for m in s["by_model"]} == {"gia-co": pytest.approx(7.0), "model-la": 0.0}
    assert [u["key"] for u in s["by_user"]] == ["an"] and len(s["by_day"]) == 1


def test_budget_modes():
    usage.record("an", "gia-co", 600, 400)
    settings.llmops.daily_token_limit = 1000
    assert usage.status()["checks"][0]["state"] == "exceeded"
    settings.llmops.budget_mode = "warn"
    assert usage.check("an") is None                            # chỉ cảnh báo
    settings.llmops.budget_mode = "off"
    assert usage.check("an") is None
    settings.llmops.budget_mode = "block"
    msg = usage.check("an")
    assert msg and "hạn mức" in msg and "1000" in msg
    settings.llmops.daily_token_limit = 100000
    assert usage.check("an") is None                            # còn dư thì không chặn


def test_warning_threshold_and_per_user_and_monthly_cost():
    usage.record("an", "gia-co", 800, 100)
    usage.record("binh", "gia-co", 10, 0)
    settings.llmops.daily_token_limit = 1000
    assert usage.status()["checks"][0]["state"] == "warn"       # 91 % >= 80 %
    settings.llmops.daily_token_limit = 0
    settings.llmops.user_daily_token_limit = 500
    settings.llmops.budget_mode = "block"
    assert usage.check("an") and usage.check("binh") is None    # hạn mức theo người: chỉ người vượt mới bị chặn
    settings.llmops.user_daily_token_limit = 0
    usage.record("an", "gia-co", 1_000_000, 0)
    settings.llmops.monthly_cost_limit = 1.0
    assert "chi phí trong tháng" in usage.check("ai-do").lower()


async def test_alert_is_sent_once_per_threshold(monkeypatch):
    sent = []

    async def fake_dispatch(title, message, **kw):
        sent.append((title, kw.get("severity")))
    from mateai.application.operations import alert_dispatcher
    monkeypatch.setattr(alert_dispatcher, "dispatch", fake_dispatch)
    settings.llmops.daily_token_limit = 1000
    for _ in range(3):
        usage.record("an", "gia-co", 400, 0)
    import asyncio
    await asyncio.sleep(0.05)
    assert [s[1] for s in sent] == ["warning", "critical"]       # 40 % không báo; 80 % báo một lần; 120 % báo một lần


def test_db_failure_never_blocks_users(monkeypatch):
    settings.llmops.budget_mode = "block"
    monkeypatch.setattr(usage, "_checks", lambda p: (_ for _ in ()).throw(RuntimeError("db down")))
    assert usage.check("an") is None


def test_save_budget_validates_and_persists(monkeypatch):
    saved = {}
    from mateai.application.administration import config_governance as gov
    monkeypatch.setattr(gov, "save_config", lambda actor, mutate, note, mask=None, **kw: (lambda cfg: (mutate(cfg), saved.update(cfg)))({}))
    out = usage.save_budget("boss", {"budget_mode": "block", "daily_token_limit": 5000, "khong-biet": 1})
    assert saved["llmops"] == {"budget_mode": "block", "daily_token_limit": 5000} and "checks" in out
    with pytest.raises(ValueError):
        usage.save_budget("boss", {"budget_mode": "lung-tung"})
    with pytest.raises(ValueError):
        usage.save_budget("boss", {"khong-biet": 1})
    with pytest.raises(ValueError):
        usage.save_budget("boss", {"warn_ratio": 5})


async def test_agent_loop_refuses_before_calling_the_model_when_budget_is_blocked(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    usage.record("an", "gia-co", 900, 200)
    settings.llmops.daily_token_limit = 1000
    settings.llmops.budget_mode = "block"

    async def never(*a, **k):
        raise AssertionError("model không được gọi khi hết ngân sách")
    monkeypatch.setattr(llm_engine, "_call_llm", never)
    out = await llm_engine.ask_async("chào", source_device="an", caller="an")
    assert out["success"] is False and out["error"] == "budget_exceeded" and "hạn mức" in out["reply"]


# ── đánh giá ────────────────────────────────────────────────────────────────

CASES = [{"id": "phep", "prompt": "Nghỉ phép năm mấy ngày?", "checks": [{"type": "contains", "value": "12 ngày"}, {"type": "not_contains", "value": "không biết"}]},
         {"id": "thiet-bi", "prompt": "Máy chủ nào đang lỗi?", "checks": [{"type": "tool_called", "value": "get_infra_status"}, {"type": "max_chars", "value": 200}]}]


def test_case_validation_is_strict():
    assert ev.validate_cases(CASES)[0]["checks"][0] == {"type": "contains", "value": "12 ngày"}
    bad = [[], "x", [{"id": "A B", "prompt": "q", "checks": [{"type": "contains", "value": "x"}]}],
           [{"id": "a", "prompt": "", "checks": [{"type": "contains", "value": "x"}]}], [{"id": "a", "prompt": "q", "checks": []}],
           [{"id": "a", "prompt": "q", "checks": [{"type": "ai-cham", "value": "x"}]}],
           [{"id": "a", "prompt": "q", "checks": [{"type": "regex", "value": "("}]}],
           [{"id": "a", "prompt": "q", "checks": [{"type": "max_chars", "value": "nhieu"}]}],
           [{"id": "a", "prompt": "q", "checks": [{"type": "contains", "value": "x"}]}] * 2]
    for raw in bad:
        with pytest.raises(ev.EvalError):
            ev.validate_cases(raw)


def test_grading_rules():
    case = {"checks": [{"type": t, "value": v} for t, v in (("contains", "NGHỈ PHÉP"), ("not_contains", "bịa"), ("regex", r"\d+ ngày"), ("tool_called", "Get_Policy"),
                                                              ("tool_not_called", "delete_item"), ("min_chars", 5), ("max_chars", 100))]}
    assert all(c["ok"] for c in ev.grade(case, "Nghỉ phép năm là 12 ngày", ["get_policy"]))
    bad = ev.grade(case, "bịa ra " + "x" * 200, ["delete_item"])
    assert [c["ok"] for c in bad] == [False, False, False, False, False, True, False]


def responder_from(replies):
    async def r(prompt, session):
        out = replies[prompt]
        if isinstance(out, Exception):
            raise out
        return out
    return r


async def test_run_compare_and_detect_regression():
    ev.save_set("hr", "Kiểm tra HR", CASES, "boss")
    good = {"Nghỉ phép năm mấy ngày?": {"reply": "Có 12 ngày phép năm.", "usage": {"total_tokens": 50}, "route_info": {"model": "gia-co"}},
            "Máy chủ nào đang lỗi?": {"reply": "srv-1", "tool_calls_made": [{"skill": "get_infra_status"}], "usage": {"total_tokens": 30}, "route_info": {"model": "gia-co"}}}
    run1 = (await _run("hr", good))
    assert run1["status"] == ev.COMPLETED and (run1["passed"], run1["total"], run1["pass_rate"]) == (2, 2, 1.0) and run1["total_tokens"] == 80
    assert run1["comparison"] is None and run1["models"] == ["gia-co"]
    worse = {**good, "Nghỉ phép năm mấy ngày?": {"reply": "Tôi không biết.", "usage": {"total_tokens": 10}}}
    run2 = await _run("hr", worse)
    assert (run2["passed"], run2["pass_rate"]) == (1, 0.5)
    assert run2["comparison"] == {"previous_run": run1["run_id"], "previous_pass_rate": 1.0, "delta": -0.5, "regressed": ["phep"], "fixed": []}
    assert [c["ok"] for c in run2["results"][0]["checks"]] == [False, False]
    assert ev.list_sets()[0]["last_run"]["run_id"] == run2["run_id"] and ev.list_sets()[0]["cases"] == 2


async def _run(set_id, replies):
    started = await ev.start(set_id, "boss", responder=responder_from(replies))
    await ev.wait(started["run_id"])
    return ev.get_run(started["run_id"])


async def test_a_crashing_case_fails_that_case_only():
    ev.save_set("hr", "HR", CASES, "boss")
    run = await _run("hr", {"Nghỉ phép năm mấy ngày?": RuntimeError("model sập"),
                            "Máy chủ nào đang lỗi?": {"reply": "ok", "tool_calls_made": [{"skill": "get_infra_status"}]}})
    assert run["status"] == ev.COMPLETED and run["passed"] == 1
    assert "model sập" in run["results"][0]["error"] and run["results"][1]["passed"] is True


async def test_budget_refusal_is_a_failure_not_a_pass():
    ev.save_set("hr", "HR", [CASES[0]], "boss")
    run = await _run("hr", {CASES[0]["prompt"]: {"reply": "Hạn mức hết", "success": False, "error": "budget_exceeded"}})
    assert run["passed"] == 0 and "budget_exceeded" in run["results"][0]["error"]


async def test_one_run_at_a_time_and_budget_gate_and_recovery():
    import asyncio
    ev.save_set("hr", "HR", [CASES[0]], "boss")
    gate = asyncio.Event()

    async def slow(prompt, session):
        await gate.wait()
        return {"reply": "12 ngày"}
    first = await ev.start("hr", "boss", responder=slow)
    with pytest.raises(ev.EvalError, match="Đang có"):
        await ev.start("hr", "boss", responder=slow)
    gate.set()
    await ev.wait(first["run_id"])
    settings.llmops.budget_mode = "block"
    settings.llmops.daily_token_limit = 1
    usage.record("an", "gia-co", 10, 10)
    with pytest.raises(ev.EvalError, match="hạn mức"):
        await ev.start("hr", "boss", responder=slow)
    db_manager.dev_insert("ai_eval_runs", {"run_id": "EVR-old", "set_id": "hr", "status": ev.RUNNING, "passed": 0, "total": 1, "total_tokens": 0, "created_at": "2026-01-01 00:00:00"})
    assert ev.recover_interrupted() == 1 and ev.get_run("EVR-old")["status"] == ev.INTERRUPTED


async def test_default_responder_offers_only_read_only_tools_to_a_low_privilege_identity(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    seen = {}

    async def fake_ask(prompt, **kw):
        seen.update(kw)
        return {"reply": "ok"}
    monkeypatch.setattr(llm_engine, "ask_async", fake_ask)
    await ev._default_responder("hỏi", "s1")
    from mateai.application.security.risk_engine import assess_risk
    assert seen["caller"] == "ai-eval" and seen["source_device"] == "ai-eval"
    assert seen["tool_names"] and all(assess_risk(n) <= 1 for n in seen["tool_names"] if n != "__none__")


# ── API ─────────────────────────────────────────────────────────────────────

def client(role):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "username": "u-" + role}
    return TestClient(app)


def test_api_permissions_and_flow(monkeypatch):
    assert client("viewer").get("/api/v1/llmops/usage").status_code == 403
    m = client("manager")
    assert m.get("/api/v1/llmops/usage?days=7").status_code == 200 and m.get("/api/v1/llmops/budget").status_code == 200
    for method, path, body in (("post", "/api/v1/llmops/budget", {"budget_mode": "warn"}), ("put", "/api/v1/llmops/eval/sets/x", {"name": "n", "cases": CASES}),
                               ("post", "/api/v1/llmops/eval/sets/x/run", None), ("delete", "/api/v1/llmops/eval/sets/x", None)):
        assert getattr(m, method)(path, **({"json": body} if body is not None else {})).status_code == 403, path
    a = client("admin")
    assert a.put("/api/v1/llmops/eval/sets/hr", json={"name": "HR", "cases": [{"id": "x"}]}).status_code == 422
    assert a.put("/api/v1/llmops/eval/sets/hr", json={"name": "HR", "cases": CASES}).json()["name"] == "HR"
    assert a.get("/api/v1/llmops/eval/sets/hr").json()["cases"][0]["id"] == "phep" and a.get("/api/v1/llmops/eval/sets/nope").status_code == 404
    assert a.post("/api/v1/llmops/budget", json={"budget_mode": "lung-tung"}).status_code == 422
    assert a.post("/api/v1/llmops/eval/sets/nope/run").status_code == 422
    assert a.get("/api/v1/llmops/eval/runs/EVR-nope").status_code == 404
    assert a.delete("/api/v1/llmops/eval/sets/hr").status_code == 200 and a.delete("/api/v1/llmops/eval/sets/hr").status_code == 404


# ── công cụ AI (chỉ đọc) ────────────────────────────────────────────────────

async def test_ai_tools_report_real_numbers_and_unpriced_tokens():
    from skills import llmops_tools as T
    usage.record("an", "gia-co", 1_000_000, 0)
    usage.record("an", "model-la", 500, 0)
    settings.llmops.daily_token_limit = 10_000_000
    u = await T.get_ai_usage(7)
    assert u["success"] and u["total"]["cost"] == pytest.approx(2.0) and u["total"]["unpriced_tokens"] == 500 and "chưa khai giá" in u["note"]
    assert u["budget_checks"][0]["key"] == "daily_tokens" and u["top_users"][0]["key"] == "an"
    b = await T.get_ai_budget_status()
    assert b["success"] and b["mode"] == "warn" and b["hint"] is None
    settings.llmops.daily_token_limit = 0
    assert "Chưa đặt hạn mức" in (await T.get_ai_budget_status())["hint"]


async def test_ai_eval_tool_shows_regressions_and_failed_checks():
    from skills import llmops_tools as T
    assert "Chưa có bộ kiểm" in (await T.get_ai_eval_results())["hint"]
    ev.save_set("hr", "HR", [CASES[0]], "boss")
    await _run("hr", {CASES[0]["prompt"]: {"reply": "12 ngày"}})
    bad = await _run("hr", {CASES[0]["prompt"]: {"reply": "Tôi không biết"}})
    listed = await T.get_ai_eval_results(set_id="hr")
    assert listed["sets"][0]["last_run"]["run_id"] == bad["run_id"]
    d = await T.get_ai_eval_results(run_id=bad["run_id"])
    assert d["comparison"]["regressed"] == ["phep"] and d["failed_cases"][0]["id"] == "phep"
    assert {c["type"] for c in d["failed_cases"][0]["failed_checks"]} == {"contains", "not_contains"}
    assert (await T.get_ai_eval_results(run_id="EVR-khong-co"))["success"] is False


def test_ai_tools_are_read_only_and_rbac_scoped():
    from mateai.application.security import security_guard as sg
    from mateai.application.security.risk_engine import assess_risk
    names = ("get_ai_usage", "get_ai_budget_status", "get_ai_eval_results")
    for name in names:
        assert assess_risk(name) == 1, name
        assert sg.security_guard._evaluate_rbac(name, "it_support", "t")[0], name      # manager -> it_support: xem được
        assert not sg.security_guard._evaluate_rbac(name, "viewer", "t")[0], name


# ── đường giọng nói (stream) ────────────────────────────────────────────────

@pytest.fixture
def voice(monkeypatch):
    from mateai.application.agent.llm_engine import llm_engine
    from mateai.infrastructure.llm.llm_provider import LLMStreamChunk
    opened = []

    class FakeProvider:
        async def stream(self, **kw):
            opened.append(kw)
            for part in ("Dạ, RAID 1 ghi ", "cùng dữ liệu lên hai ổ."):
                yield LLMStreamChunk(content=part, model="gia-co")
            yield LLMStreamChunk(model="gia-co", usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000})

    async def no_client():
        return None
    monkeypatch.setattr(llm_engine, "_ensure_shared_client", no_client)
    monkeypatch.setattr(llm_engine, "get_provider", lambda brain_role="voice": FakeProvider())
    monkeypatch.setattr(llm_engine, "classify_intent", staticmethod(lambda q: {"type": "conversation", "target_brain": "voice", "ack_needed": False}))

    async def run(query="RAID 1 là gì?"):
        turn = {}
        out = [s async for s in llm_engine.stream_voice_response(query, history=[], source_device="robot-1", session_id="s-voice", turn=turn, caller="an")]
        return out, turn
    return run, opened


async def test_voice_stream_records_the_usage_the_provider_reports(voice):
    run, opened = voice
    out, turn = await run()
    assert "".join(out).startswith("Dạ, RAID 1") and turn["usage"] == {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}
    s = usage.summary(1)
    assert s["by_user"][0]["key"] == "an" and s["total"]["tokens"] == 1_100_000 and s["total"]["cost"] == pytest.approx(3.0)


async def test_voice_stream_is_refused_before_the_model_when_budget_is_blocked(voice):
    run, opened = voice
    usage.record("an", "gia-co", 900, 200)
    settings.llmops.daily_token_limit = 1000
    settings.llmops.budget_mode = "block"
    out, turn = await run()
    assert opened == [] and turn["error"] == "budget_exceeded" and "hạn mức" in " ".join(out)


async def test_providers_ask_for_stream_usage_and_surface_the_final_usage_chunk(monkeypatch):
    from types import SimpleNamespace
    from mateai.infrastructure.llm import llm_provider as lp
    sent = []

    async def gen():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="chào", tool_calls=None), finish_reason=None)], usage=None)
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3))

    async def create(**kw):
        sent.append(kw)
        return gen()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    p = lp.DirectLLMProvider(client, "gia-co", "http://x")
    chunks = [c async for c in p.stream(messages=[{"role": "user", "content": "hi"}])]
    assert sent[0]["stream_options"] == {"include_usage": True}
    assert chunks[0].content == "chào" and chunks[-1].usage == {"prompt_tokens": 12, "completion_tokens": 3}
    monkeypatch.setattr(settings.llmops, "stream_usage", False)
    [c async for c in p.stream(messages=[{"role": "user", "content": "hi"}])]
    assert "stream_options" not in sent[1]                     # router từ chối tham số -> tắt được
