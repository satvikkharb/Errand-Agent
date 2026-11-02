"""Builds what the model sees each turn, under a hard token budget, and reports exactly what went in.

Priority when space is short: system rules > saved request > current message and this run's tool
results > relevant memories > earlier-conversation summary > older chat turns.
Only confirmed, current, non-forgotten memories are ever candidates (the app store enforces that).
"""
import copy
from dataclasses import dataclass
from datetime import datetime

from app.agent import prompt
from app.agent.tokens import count_tokens, message_tokens, messages_tokens

STUB = "[result omitted to stay within the context budget]"
CORE_KEYS = {"preferred_area", "earliest_time", "latest_time", "preferred_time_of_day", "avoid_provider",
             "weekend_ok", "home_type", "language"}
SERVICE_KEYS = {"plumber": ("preferred_plumber", "default_budget_plumber"),
                "electrician": ("preferred_electrician", "default_budget_electrician"),
                "ac_servicing": ("preferred_ac_servicing", "default_budget_ac")}


@dataclass
class BuiltContext:
    messages: list[dict]
    report: dict


def rank_memories(mems: list[dict], service: str | None) -> list[dict]:
    """Relevant first. Other services' keys are excluded. Everything else is low priority filler."""
    mine = set(SERVICE_KEYS.get(service, ()))
    others = {k for s, ks in SERVICE_KEYS.items() if s != service for k in ks} if service else set()

    def score(m):
        if m["key"] in CORE_KEYS or m["key"] in mine:
            return 10
        if m["key"] in others:
            return -1
        if not service and any(m["key"] in ks for ks in SERVICE_KEYS.values()):
            return 5
        return 1

    scored = [(score(m), m) for m in mems]
    scored = [(s, m) for s, m in scored if s >= 0]
    scored.sort(key=lambda sm: (-sm[0], str(sm[1].get("updated_at")), sm[1]["key"]))
    return [m for _, m in scored]


class ContextBuilder:
    def __init__(self, budget_tokens: int = 6000, max_memories: int = 8, history_limit: int = 30):
        self.budget, self.max_memories, self.history_limit = budget_tokens, max_memories, history_limit

    def build(self, *, now: datetime, session: dict, memories: list[dict], summaries: list[dict],
              history: list[dict], run_messages: list[dict], tools_tokens: int = 0,
              pending_approval: dict | None = None) -> BuiltContext:
        frame = session["request_frame"]
        head = [{"role": "system", "content": prompt.system_prompt(now)},
                {"role": "system", "content": prompt.render_frame(session)}]
        for block in (prompt.render_search(frame), prompt.render_suggestion(frame), prompt.render_avail(frame),
                      prompt.render_pending(pending_approval)):
            if block:
                head.append({"role": "system", "content": block})
        current = history[-1] if history and history[-1]["role"] == "user" else None
        past = history[:-1] if current else history
        cur_msgs = [{"role": "user", "content": current["content"]}] if current else []
        run = copy.deepcopy(run_messages)

        used = tools_tokens + messages_tokens(head) + messages_tokens(cur_msgs) + messages_tokens(run)
        stubbed = 0
        while used > self.budget:  # make room by dropping the oldest tool results of this run
            idx = next((i for i, m in enumerate(run) if m["role"] == "tool" and m["content"] != STUB), None)
            if idx is None:
                break
            used -= message_tokens(run[idx])
            run[idx]["content"] = STUB
            used += message_tokens(run[idx])
            stubbed += 1

        # a finished errand's service is stale: a new errand may be for any service
        service = None if session["state"] in ("DONE", "FAILED") else frame.get("service")
        ranked = rank_memories(memories, service)[: self.max_memories]
        chosen: list[dict] = []
        header = count_tokens("Stored preferences (confirmed by the user):\n") + 4
        for m in ranked:
            cost = count_tokens(f"- {m['key']}: {m['value']}\n") + (header if not chosen else 0)
            if used + cost > self.budget:
                break
            chosen.append(m)
            used += cost
        mem_block = prompt.render_memories(chosen)

        mine = [x for x in summaries if x.get("session_id") == session["id"]][-1:]  # this chat's rolling summary
        other = [x for x in summaries if x.get("session_id") != session["id"]][-1:]  # latest earlier conversation
        sum_used: list[dict] = []
        for x in mine + other:  # this chat matters most, so it gets budget first
            cost = count_tokens(x["text"]) + 10
            if used + cost <= self.budget:
                sum_used.append(x)
                used += cost
        ordered = [x for x in sum_used if x in other] + [x for x in sum_used if x in mine]
        sum_block = ("Earlier conversation summary: " + " ".join(x["text"] for x in ordered)) if ordered else None
        # turns the summary already stands in for are not sent twice
        covered = max((x["covers_up_to_seq"] for x in sum_used if x in mine), default=0)
        recent = [m for m in past if m["seq"] > covered]

        kept: list[dict] = []
        for m in reversed(recent[-self.history_limit:]):
            cost = message_tokens(m)
            if used + cost > self.budget:
                break
            kept.append(m)
            used += cost
        kept.reverse()

        pre = [{"role": "system", "content": head[0]["content"]}]
        if mem_block:
            pre.append({"role": "system", "content": mem_block})
        if sum_block:
            pre.append({"role": "system", "content": sum_block})
        messages = pre + head[1:] + [{"role": m["role"], "content": m["content"]} for m in kept] + cur_msgs + run
        report = {
            "budget": self.budget, "tokens": messages_tokens(messages) + tools_tokens, "tools_tokens": tools_tokens,
            "memory_ids": [m["id"] for m in chosen], "memory_keys": [m["key"] for m in chosen],
            "memories_dropped": len(memories) - len(chosen),
            "summary_ids": [s["id"] for s in sum_used],
            "history_message_ids": [m["id"] for m in kept], "history_dropped": len(recent) - len(kept),
            "history_covered_by_summary": len(past) - len(recent),
            "tool_results_stubbed": stubbed}
        return BuiltContext(messages, report)
