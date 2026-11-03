"use client";

import { useSyncExternalStore } from "react";
import type { ApprovalCard, ChatResult, MemoryPrompt } from "@/lib/api";
import { bookingKey, parseCard } from "@/lib/cards";
import type { AgentOptions } from "@/lib/options";

// Client-side chat state, persisted in localStorage so a reload resumes the same backend session.
// It is a view over what the backend returned (replies, approval cards, memory prompts), plus a
// local list of errands booked from this device (the backend has no "list bookings" endpoint yet).

/** pending: waiting for the user. processing: executor outcome unclear (RECONCILE). */
export type CardState = "pending" | "approved" | "processing" | "declined" | "failed" | "stale";

export type Item =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "agent"; text: string; runId?: string | null; options?: AgentOptions }
  | { id: string; kind: "notice"; text: string }
  | { id: string; kind: "error"; text: string }
  | { id: string; kind: "card"; card: ApprovalCard; state: CardState }
  | { id: string; kind: "memory"; prompt: MemoryPrompt; state: "open" | "yes" | "no" };

export type Errand = { id: string; key: string; title: string; note: string };

export type ChatState = {
  userId: string | null;
  sessionId: string | null;
  items: Item[];
  errands: Errand[];
  /** The booking card the user approved and is now paying for on Hippopay. */
  checkout: ApprovalCard | null;
};

const KEY = "errand.chat.v2";
const EMPTY: ChatState = { userId: null, sessionId: null, items: [], errands: [], checkout: null };

const uid = () => Math.random().toString(36).slice(2, 10);

let cache: ChatState | null = null;
const listeners = new Set<() => void>();

function load(): ChatState {
  if (cache) return cache;
  try {
    const raw = localStorage.getItem(KEY);
    cache = raw ? { ...EMPTY, ...(JSON.parse(raw) as ChatState) } : EMPTY;
  } catch {
    cache = EMPTY;
  }
  return cache;
}

function commit(next: ChatState) {
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {}
  listeners.forEach((l) => l());
}

export function setChat(update: (s: ChatState) => ChatState) {
  commit(update(load()));
}

export function resetChat() {
  commit(EMPTY);
}

/** Never show one person's thread or errands to another. Call once the session is known. */
export function ensureUser(userId: string) {
  if (load().userId !== userId) commit({ ...EMPTY, userId });
}

/** The backend lost this conversation: forget its id and grey out any card that can no longer be used. */
export function forgetSession() {
  setChat((s) => ({
    ...s,
    sessionId: null,
    checkout: null,
    items: s.items.map((i) => (i.kind === "card" && i.state === "pending" ? { ...i, state: "stale" as const } : i)),
  }));
}

export function startNewErrand() {
  setChat((s) => ({ ...s, sessionId: null, items: [], checkout: null }));
}

type NewItem<T = Item> = T extends Item ? Omit<T, "id"> : never;

export const addItem = (item: NewItem) =>
  setChat((s) => ({ ...s, items: [...s.items, { id: uid(), ...item } as Item] }));

/** Turns a backend ChatResult into chat items, in the order a person should read them. */
function withResult(
  s: ChatState,
  res: ChatResult,
  opts: { supersede: boolean; options?: AgentOptions | null },
): ChatState {
  const add: Item[] = [];
  for (const n of res.notices ?? []) add.push({ id: uid(), kind: "notice", text: n });
  for (const v of res.user_views ?? []) {
    if (typeof v === "string") add.push({ id: uid(), kind: "agent", text: v });
  }
  if (res.reply) {
    add.push({ id: uid(), kind: "agent", text: res.reply, runId: res.run_id, options: opts.options ?? undefined });
  }

  const card = res.approval_card;
  const known = card && s.items.some((i) => i.kind === "card" && i.card.approval_id === card.approval_id);
  if (card && !known) add.push({ id: uid(), kind: "card", card, state: "pending" });
  if (res.memory_prompt) add.push({ id: uid(), kind: "memory", prompt: res.memory_prompt, state: "open" });

  // The backend voids an open card when the user changes the request, so older pending cards are stale.
  const items = s.items.map((i) =>
    opts.supersede && i.kind === "card" && i.state === "pending" && i.card.approval_id !== card?.approval_id
      ? { ...i, state: "stale" as const }
      : i,
  );
  return { ...s, sessionId: res.session_id ?? s.sessionId, items: [...items, ...add] };
}

export const recordChat = (res: ChatResult, options?: AgentOptions | null) =>
  setChat((s) => withResult(s, res, { supersede: true, options }));

/** The user confirmed a provider + slot on a picker: keep it highlighted and read-only. */
export function setPicked(itemId: string, picked: { providerId: string; slotId: string }) {
  setChat((s) => ({
    ...s,
    items: s.items.map((i) =>
      i.id === itemId && i.kind === "agent" && i.options ? { ...i, options: { ...i.options, picked } } : i,
    ),
  }));
}

export function setCardState(approvalId: string, state: CardState) {
  setChat((s) => ({
    ...s,
    items: s.items.map((i) => (i.kind === "card" && i.card.approval_id === approvalId ? { ...i, state } : i)),
  }));
}

export function setMemoryState(memoryId: string, state: "yes" | "no") {
  setChat((s) => ({
    ...s,
    items: s.items.map((i) => (i.kind === "memory" && i.prompt.memory_id === memoryId ? { ...i, state } : i)),
  }));
}

/** Maps the session state after /approve to what the card should show. */
export function outcomeOf(res: ChatResult): CardState {
  if (res.busy) return "pending"; // nothing happened, the card is still live
  if (res.state === "DONE") return "approved";
  if (res.state === "RECONCILE" || res.state === "EXECUTE") return "processing";
  return "failed"; // SEARCH / FAILED / expired: the backend says nothing was booked
}

/** Applies the result of approving (and so executing) a card. Returns what happened. */
export function recordApproval(card: ApprovalCard, res: ChatResult): CardState {
  const outcome = outcomeOf(res);
  setChat((s) => {
    let next = withResult(s, res, { supersede: false });
    next = {
      ...next,
      checkout: null,
      items: next.items.map((i) =>
        i.kind === "card" && i.card.approval_id === card.approval_id && outcome !== "pending"
          ? { ...i, state: outcome }
          : i,
      ),
    };
    if (outcome === "approved") next = { ...next, errands: applyToErrands(next.errands, card) };
    return next;
  });
  return outcome;
}

export function recordReject(card: ApprovalCard, res: ChatResult) {
  setChat((s) => {
    const next = withResult(s, res, { supersede: false });
    return {
      ...next,
      checkout: null,
      items: next.items.map((i) =>
        i.kind === "card" && i.card.approval_id === card.approval_id ? { ...i, state: "declined" as const } : i,
      ),
    };
  });
}

function applyToErrands(errands: Errand[], card: ApprovalCard): Errand[] {
  const p = parseCard(card);
  const key = bookingKey(card);
  if (card.action === "book") {
    return [{ id: uid(), key, title: p.what, note: `Booked for ${p.when}` }, ...errands];
  }
  if (card.action === "cancel") return errands.filter((e) => e.key !== key);
  return errands.map((e) => (e.key === key ? { ...e, key: `${p.what}|${p.to?.replace(/^To /, "")}`, note: `Moved to ${p.to?.replace(/^To /, "")}` } : e));
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function useChat(): ChatState {
  return useSyncExternalStore(subscribe, load, () => EMPTY);
}
