import type { Span } from "@/lib/api";

// The backend's reply to "show me slots" is prose, but the search it ran is recorded structurally
// in the run trace (GET /runs/{id}/trace): matches with rating, price and slot ids. We read that
// to draw a proper provider / rating / slots picker instead of a numbered list.

export type Slot = { id: string; start: string; end: string; label: string };
export type ProviderOption = {
  rank: number;
  id: string;
  name: string;
  rating: number;
  price: number;
  visit_fee?: number;
  slots: Slot[];
};

export type AgentOptions = {
  /** "Plumber in Koramangala, Mon 9 Nov to Sun 15 Nov, 12 pm to 6 pm" */
  title?: string;
  matches: ProviderOption[];
  /** Set once the user has confirmed a choice: the picker becomes read-only. */
  picked?: { providerId: string; slotId: string };
};

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);

/** The most recent search a run performed, as options. Null if the run searched nothing useful. */
export function extractOptions(spans: Span[]): AgentOptions | null {
  let matches: ProviderOption[] | null = null;
  let title: string | undefined;

  for (const s of spans) {
    const out = s.output;
    if (s.kind !== "tool_call" || !isObj(out)) continue;

    if (isObj(out.frame) && isObj(out.in_words)) {
      const f = out.frame;
      const w = out.in_words;
      const what = [cap(f.service), f.area ? `in ${String(f.area)}` : ""].filter(Boolean).join(" ");
      const when = [w.date, w.time].filter((x) => typeof x === "string" && x).join(", ");
      title = [what, when].filter(Boolean).join(" · ");
    }

    // search results ride on update_request_frame ({search: {matches}}) or a search tool ({matches})
    const found = isObj(out.search) ? out.search.matches : out.matches;
    if (Array.isArray(found)) {
      const ok = found.filter(isOption).map((m) => ({ ...m, slots: m.slots.filter(isSlot) }));
      matches = ok.filter((m) => m.slots.length > 0);
    }
  }
  return matches && matches.length > 0 ? { title, matches } : null;
}

const cap = (v: unknown) => (typeof v === "string" && v ? v[0].toUpperCase() + v.slice(1) : "");
const isSlot = (v: unknown): v is Slot => isObj(v) && typeof v.id === "string" && typeof v.label === "string";
const isOption = (v: unknown): v is ProviderOption =>
  isObj(v) && typeof v.id === "string" && typeof v.name === "string" && typeof v.price === "number" && Array.isArray(v.slots);

const LIST_LINE = /^\s*(?:\d+[.)]|[-•*])\s+/;

/**
 * The model usually writes "intro, 1. 2. 3., closing question". When we draw the picker, the numbered
 * list is redundant, so split the reply around it: text before, text after. With no list, everything
 * is "before" and the picker follows it.
 */
export function splitAroundList(text: string): { before: string; after: string } {
  const lines = text.split("\n");
  const first = lines.findIndex((l) => LIST_LINE.test(l));
  if (first === -1) return { before: text.trim(), after: "" };
  let last = first;
  lines.forEach((l, i) => {
    if (LIST_LINE.test(l)) last = i;
  });
  return {
    before: lines.slice(0, first).join("\n").trim(),
    after: lines.slice(last + 1).join("\n").trim(),
  };
}
