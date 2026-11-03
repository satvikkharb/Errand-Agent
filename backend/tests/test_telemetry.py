"""LangSmith: every run is a trace, every step a child run. Uses the REAL langsmith SDK against a local fake server."""
import json
import logging

import pytest
from langsmith import Client

from app.agent.telemetry import Telemetry
from app.config import Settings
from app.llm import call, say
from app.tools.sim import provider_cancels
from tests.support.fake_langsmith import FakeLangSmith
from tests.test_orchestrator import FIRST_MSG, H, find_steps


@pytest.fixture(autouse=True)
def _no_real_langsmith_env(monkeypatch):
    """Settings reads the process environment too: never let a developer's real keys change what these tests see."""
    for k in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT",
              "LANGSMITH_WORKSPACE_ID", "LANGSMITH_SEND_PROMPTS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def ls():
    fs = FakeLangSmith()
    tel = Telemetry(Client(api_url=fs.url, api_key="test-key"), "errand-test")
    fs.tel = tel
    yield fs, tel
    tel.drain()
    fs.close()


def wired(h, tel):
    h.o.telemetry = h.o.proactive.telemetry = tel
    return h


def root_of(fs, name):
    fs.tel.drain()  # sends happen in the background
    roots = [r for r in fs.by_name(name) if r.get("parent_run_id") is None]
    assert roots, f"no root run named {name}: {[r.get('name') for r in fs.runs.values()]}"
    return roots[-1]


# ================================================================ one trace per turn, every step a child
def test_a_chat_turn_is_one_trace_with_every_model_call_tool_call_and_step(ls):
    fs, tel = ls
    h = wired(H(), tel)
    r = h.chat(FIRST_MSG, *find_steps())
    root = root_of(fs, "chat")
    assert root["inputs"] == {"input": FIRST_MSG} and root["outputs"]["reply"] == r.reply and root["session_name"] == "errand-test"
    kids = fs.children_of(root)
    by_type = lambda t: [k for k in kids if k["run_type"] == t]  # noqa: E731
    assert len(by_type("llm")) == 3  # the model was called three times: fill the request, search, answer
    assert [k["name"] for k in by_type("tool")] == ["update_request_frame", "search_providers"]
    assert any(k["name"] == "context: context" for k in kids) and any(k["name"].startswith("state: ") for k in kids)
    assert all(k["parent_run_id"] == root["id"] for k in kids)  # one flat, ordered trace
    assert root["extra"]["metadata"]["thread_id"] == r.session_id == root["extra"]["metadata"]["session_id"]  # LangSmith Threads
    assert {"chat"} <= set(root["tags"])


def test_model_runs_carry_the_prompt_the_reply_tokens_and_the_model(ls):
    fs, tel = ls
    h = wired(H(), tel)
    r = h.chat(FIRST_MSG, *find_steps())
    llm = [k for k in fs.children_of(root_of(fs, "chat")) if k["run_type"] == "llm"]
    first = llm[0]
    sent = first["inputs"]["messages"]
    assert sent[0]["role"] == "system" and any(m["role"] == "user" and FIRST_MSG in m["content"] for m in sent)  # the exact prompt
    msg = first["outputs"]["choices"][0]["message"]
    assert msg["tool_calls"][0]["name"] == "update_request_frame"  # what the model decided to do
    usage = first["outputs"]["usage_metadata"]
    assert usage["input_tokens"] > 0 and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
    assert first["extra"]["metadata"]["ls_provider"] == "openai" and first["extra"]["metadata"]["ls_model_name"] == "scripted"
    assert first["extra"]["metadata"]["estimated_cost_usd"] is not None
    assert llm[1]["outputs"]["choices"][0]["message"]["tool_calls"][0]["name"] == "search_providers"
    assert llm[2]["outputs"]["choices"][0]["message"]["content"] == r.reply  # the final answer the user saw


def test_tool_calls_show_their_arguments_and_sanitized_results(ls):
    fs, tel = ls
    h = wired(H(), tel)
    h.chat(FIRST_MSG, *find_steps())
    tool = next(k for k in fs.children_of(root_of(fs, "chat")) if k["name"] == "update_request_frame")
    assert tool["inputs"]["service"] == "plumber" and tool["inputs"]["budget_max"] == 1500
    assert tool["outputs"]["status"] == "ok" and tool["outputs"]["search"]["status"] == "ok"  # the search ran inside it
    assert tool["start_time"] <= tool["end_time"] and tool["tags"] == ["tool_call"]


# ================================================================ the booking side: approve, outcomes, scores
def test_approving_is_a_trace_of_its_own_with_each_executor_step_and_a_score(ls):
    fs, tel = ls
    h = wired(H(forced_book_outcomes=("timeout_success",)), tel)
    aid = h.to_card()
    out = h.o.approve("user_demo", aid)
    assert out.reply.startswith("Booked and verified")
    root = root_of(fs, "approve")
    steps = [(k["name"], k.get("error")) for k in fs.children_of(root) if k["run_type"] == "tool"]
    assert steps[0] == ("exec.book", "ToolTimeout") and steps[1][0] == "exec.get_booking_status"  # timeout, then the status check
    assert "outcome:booked" in root["tags"] and root["outputs"]["exit_reason"] == "done"
    fs.tel.drain()
    assert [(f["key"], f["score"]) for f in fs.feedback] == [("booking_verified", 1.0)]
    assert fs.feedback[0]["session_id"] == fs.project_id and fs.feedback[0]["run_id"] == root["id"]  # scored against the right run


def test_a_failed_action_scores_zero_and_is_tagged(ls):
    fs, tel = ls
    h = wired(H(forced_book_outcomes=("slot_taken",)), tel)
    h.o.approve("user_demo", h.to_card())
    root = root_of(fs, "approve")
    fs.tel.drain()
    assert "outcome:slot_taken" in root["tags"] and [(f["key"], f["score"]) for f in fs.feedback] == [("action_failed", 0.0)]


def test_tapping_a_slot_is_traced_and_makes_no_model_call(ls):
    fs, tel = ls
    h = wired(H(), tel)
    from tests.test_options import ask_week
    r = ask_week(h)
    p, s = r.options["matches"][0], r.options["matches"][0]["slots"][0]
    h.o.select_slot("user_demo", r.session_id, p["id"], s["id"])
    root = root_of(fs, "select_slot")
    assert root["inputs"] == {"provider_id": p["id"], "slot_id": s["id"]}
    assert not [k for k in fs.children_of(root) if k["run_type"] == "llm"]  # a tap never involves the model
    assert [k["name"] for k in fs.children_of(root) if k["run_type"] == "tool"] == ["propose_book"]


def test_a_proactive_follow_up_is_traced_as_an_event(ls):
    fs, tel = ls
    h = wired(H(), tel)
    for p in ("ac_02", "ac_03"):
        for hh in ("09", "11", "14", "16"):
            h.store.set_slot_status(f"{p}_1105_{hh}", "free")
    provider_cancels(h.store, "bk_004")
    h.o.tick()
    root = root_of(fs, "event:provider_cancelled")
    assert root["outputs"]["has_replacement"] is True and any(k["name"] == "event: provider_cancelled" for k in fs.children_of(root))
    assert {"update_request_frame", "search_providers"} <= {k["name"] for k in fs.children_of(root) if k["run_type"] == "tool"}


# ================================================================ safety signals show up in the trace
def test_blocked_injection_attempts_are_tagged_and_counted(ls):
    fs, tel = ls
    h = wired(H(), tel)
    h.chat("tell me about pl_03", call("get_provider", provider_id="pl_03"), say("It has reviews."))
    root = root_of(fs, "chat")
    assert "injection-blocked" in root["tags"] and root["extra"]["metadata"]["injection_attempts_hidden"] >= 1
    child = next(k for k in fs.children_of(root) if k["name"] == "get_provider")
    assert child["extra"]["metadata"]["injection_attempts_hidden"] >= 1
    assert "ignore all previous instructions" not in json.dumps(fs.runs).lower()  # the poison itself never leaves


def test_limits_and_crashes_are_visible_on_the_root_run(ls):
    fs, tel = ls
    h = wired(H(max_tools=3), tel)
    h.chat("loop", *[call("list_bookings")] * 5)
    assert "exit:limit_tools" in root_of(fs, "chat")["tags"]
    h2 = wired(H(), tel)
    h2.chat("boom", RuntimeError("model exploded"))
    assert root_of(fs, "chat")["error"] == "run failed" or any(r.get("error") == "run failed" for r in fs.runs.values())


# ================================================================ privacy and resilience
def test_prompt_text_can_be_switched_off_but_timings_and_tokens_stay(ls):
    fs, tel = ls
    tel.send_prompts = False
    h = wired(H(), tel)
    h.chat(FIRST_MSG, *find_steps())
    llm = next(k for k in fs.children_of(root_of(fs, "chat")) if k["run_type"] == "llm")
    assert "messages" not in llm["inputs"] and llm["outputs"]["choices"][0]["message"]["content"] == "[hidden]"
    assert llm["outputs"]["usage_metadata"]["input_tokens"] > 0


def test_prompts_are_never_written_to_our_own_database():
    h = H()
    h.chat(FIRST_MSG, *find_steps())
    spans = [s for rows in h.app._spans.values() for s in rows]
    assert spans and not any(k.startswith("ls_") for s in spans for k in s) and "You are Errand Agent" not in json.dumps(spans)


def test_a_broken_langsmith_never_breaks_a_request(caplog):
    class Dead:
        def __getattr__(self, name):
            raise ConnectionError("LangSmith is down")
    h = wired(H(), Telemetry(Dead(), "x"))
    with caplog.at_level(logging.WARNING):
        r = h.chat(FIRST_MSG, *find_steps())
    assert r.reply == "Top pick is QuickFix, rated 4.7 at ₹1200." and r.exit_reason == "done" and r.options  # the app is unaffected
    assert any("LangSmith" in m for m in caplog.messages)  # but it says so in the log


def test_an_unreachable_endpoint_does_not_slow_or_fail_the_app():
    import time
    tel = Telemetry(Client(api_url="http://127.0.0.1:9", api_key="k"), "x")  # nothing listens here
    h = wired(H(), tel)
    t0 = time.perf_counter()
    r = h.chat(FIRST_MSG, *find_steps())
    h.o.approve("user_demo", h.to_card())
    assert r.exit_reason == "done" and time.perf_counter() - t0 < 2.0  # was 12 s when flushing in the request


# ================================================================ configuration
def test_it_is_off_unless_enabled_and_keyed():
    assert Telemetry.from_settings(Settings(_env_file=None)) is None
    assert Telemetry.from_settings(Settings(_env_file=None, langsmith_tracing=True)) is None  # no key
    assert Telemetry.from_settings(Settings(_env_file=None, langsmith_api_key="lsv2_x")) is None  # not switched on
    tel = Telemetry.from_settings(Settings(_env_file=None, langsmith_tracing=True, langsmith_api_key="lsv2_x", langsmith_project="demo",
                                           langsmith_endpoint="https://eu.api.smith.langchain.com", langsmith_workspace_id="ws-1"))
    assert tel.project == "demo" and tel.client.api_url == "https://eu.api.smith.langchain.com" and tel.send_prompts is True


def test_settings_read_the_documented_variables(monkeypatch):
    for k, v in {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "lsv2_abc", "LANGSMITH_PROJECT": "errand-agent",
                 "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com", "LANGSMITH_WORKSPACE_ID": "w1",
                 "LANGSMITH_SEND_PROMPTS": "false"}.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert (s.langsmith_tracing, s.langsmith_api_key, s.langsmith_project, s.langsmith_workspace_id, s.langsmith_send_prompts) \
        == (True, "lsv2_abc", "errand-agent", "w1", False)
