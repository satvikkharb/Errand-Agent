"use client";

import { useSyncExternalStore } from "react";
import type { Message, Proposal } from "@/lib/agent";

// Chat state lives in a tiny store (backed by sessionStorage) instead of component state, so the
// payment page can mark a booking as paid and the chat page simply reflects it. Reset on logout.

export type Errand = { id: string; title: string; note: string; done?: boolean };
export type ProposalState = "pending" | "approved" | "declined";

export type ChatState = {
  messages: Message[];
  states: Record<string, ProposalState>;
  errands: Errand[];
  /** The proposal the user approved and is now paying for on Hippopay. */
  checkout: Proposal | null;
};

const KEY = "errand.chat";

export const SEED_ERRANDS: Errand[] = [
  { id: "s1", title: "Socket repair", note: "Booked for Thursday, 10:00" },
  { id: "s2", title: "AC service", note: "Done, 12 Oct", done: true },
  { id: "s3", title: "Kitchen sink unblocked", note: "Done, 3 Sep", done: true },
];

const EMPTY: ChatState = { messages: [], states: {}, errands: SEED_ERRANDS, checkout: null };

let cache: ChatState | null = null;
const listeners = new Set<() => void>();

function load(): ChatState {
  if (cache) return cache;
  try {
    const raw = sessionStorage.getItem(KEY);
    cache = raw ? { ...EMPTY, ...(JSON.parse(raw) as ChatState) } : EMPTY;
  } catch {
    cache = EMPTY;
  }
  return cache;
}

function commit(next: ChatState) {
  cache = next;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(next));
  } catch {}
  listeners.forEach((l) => l());
}

export function setChat(update: (s: ChatState) => ChatState) {
  commit(update(load()));
}

export function resetChat() {
  commit(EMPTY);
}

/** Called by the payment page once Hippopay says yes: books the errand and tells the chat. */
export function completePayment(p: Proposal) {
  setChat((s) => ({
    ...s,
    checkout: null,
    states: { ...s.states, [p.id]: "approved" },
    errands: [
      {
        id: Math.random().toString(36).slice(2, 9),
        title: `${p.trade}, ${p.provider.split(",")[0]}`,
        note: `Booked for ${p.when.split(",")[0].replace("Tomorrow", "tomorrow")}`,
      },
      ...s.errands,
    ],
    messages: [
      ...s.messages,
      {
        id: Math.random().toString(36).slice(2, 9),
        role: "agent",
        text: `Paid and booked! ${p.provider.split(" ")[0]} is coming ${p.when.toLowerCase()}. You'll get a nudge an hour before they arrive. 🍊`,
      },
    ],
  }));
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
