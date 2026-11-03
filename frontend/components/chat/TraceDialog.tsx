"use client";

import { useEffect, useState } from "react";
import { api, type Span } from "@/lib/api";

// Every run the agent does is traced by the backend (GET /runs/{id}/trace). This is a quiet,
// "show your working" view of it: model calls, tool calls and state moves.
export function TraceDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .trace(runId)
      .then((r) => live && setSpans(r.spans))
      .catch((e: Error) => live && setError(e.message));
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      live = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [runId, onClose]);

  const cost = spans?.reduce((n, s) => n + (s.cost_usd ?? 0), 0) ?? 0;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <button aria-label="Close" className="absolute inset-0 bg-ink/40" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-label="How I got there" className="rise relative flex max-h-[80vh] w-full max-w-[560px] flex-col rounded-3xl bg-sheet p-6 shadow-xl">
        <h2 className="text-[22px] font-bold tracking-tight">How I got there</h2>
        <p className="mt-1 text-[14px] text-muted">
          Every step of this reply, straight from the run trace.
          {spans && ` About $${cost.toFixed(4)} of model time.`}
        </p>
        <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
          {error && <p className="text-bad">{error}</p>}
          {!spans && !error && <p className="text-muted">Loading...</p>}
          <ol className="space-y-1.5">
            {spans
              ?.filter((s) => s.kind !== "context")
              .map((s, i) => (
                <li key={s.id ?? i} className="flex items-baseline justify-between gap-3 rounded-xl bg-raised px-3 py-2 text-[14px]">
                  <span className="min-w-0">
                    <span className="text-muted">{label(s.kind)} </span>
                    <span className="font-medium">{s.name}</span>
                    {s.error && <span className="text-bad"> ({s.error})</span>}
                  </span>
                  <span className="shrink-0 text-[12px] text-muted">
                    {s.latency_ms ? `${s.latency_ms} ms` : ""}
                    {s.tokens_in ? ` · ${s.tokens_in + (s.tokens_out ?? 0)} tok` : ""}
                  </span>
                </li>
              ))}
          </ol>
        </div>
        <button onClick={onClose} className="mt-4 h-11 rounded-full bg-orange font-semibold">
          Close
        </button>
      </div>
    </div>
  );
}

function label(kind: string) {
  return (
    { model_call: "Thought:", tool_call: "Used tool:", state_transition: "Moved to", approval: "Approval:" }[kind] ??
    `${kind}:`
  );
}
