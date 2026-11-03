"use client";

import { useSyncExternalStore } from "react";

// Mock auth: a session lives in localStorage. The backend has no auth yet and trusts the `user_id`
// string it is sent, so we derive a stable one from the email. Swap for Supabase auth later and
// use the real user id here.
export type Plan = "free" | "plus";
export type Session = { name: string; email: string; plan: Plan; userId: string };

/** Backend limit is 64 chars. "satvik@home.com" -> "u_satvik_home_com". */
export function userIdFor(email: string) {
  return `u_${email.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`.slice(0, 64);
}

const KEY = "errand.session";
const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
  };
}

function readRaw(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function emit() {
  listeners.forEach((l) => l());
}

export function signIn(s: Session) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {}
  emit();
}

export function signOut() {
  try {
    localStorage.removeItem(KEY);
  } catch {}
  emit();
}

export function setPlan(plan: Plan) {
  const raw = readRaw();
  if (!raw) return;
  signIn({ ...(JSON.parse(raw) as Session), plan });
}

const noopSubscribe = () => () => {};

/** `ready` is false during SSR and the first client render, so pages can avoid flashing the wrong state. */
export function useSession(): { session: Session | null; ready: boolean } {
  const raw = useSyncExternalStore(subscribe, readRaw, () => null);
  const ready = useSyncExternalStore(noopSubscribe, () => true, () => false);
  let session: Session | null = null;
  try {
    const parsed = raw ? (JSON.parse(raw) as Omit<Session, "userId"> & { userId?: string }) : null;
    session = parsed ? { ...parsed, userId: parsed.userId ?? userIdFor(parsed.email) } : null;
  } catch {}
  return { session, ready };
}
