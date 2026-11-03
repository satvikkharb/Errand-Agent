"use client";

import Link from "next/link";
import { Orange } from "@/components/Orange";
import { Logo } from "@/components/Logo";
import type { Notification } from "@/lib/api";
import type { Errand } from "@/lib/chat";
import type { Session } from "@/lib/session";

type Props = {
  session: Session;
  errands: Errand[];
  updates: Notification[];
  used: number;
  limit: number;
  onNew: () => void;
  onSignOut: () => void;
  onReadUpdate: (id: string) => void;
};

export function Sidebar({ session, errands, updates, used, limit, onNew, onSignOut, onReadUpdate }: Props) {
  const plus = session.plan === "plus";
  return (
    <div className="flex h-full flex-col gap-5 p-4">
      <div className="flex items-center justify-between px-1 pt-1">
        <Logo href="/chat" />
      </div>

      <button
        onClick={onNew}
        className="h-11 rounded-full bg-orange font-semibold transition-transform hover:-translate-y-0.5 active:translate-y-0"
      >
        New errand
      </button>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto">
        {updates.length > 0 && (
          <section aria-label="Updates">
            <p className="mb-2 px-2 text-[13px] font-semibold text-muted">Updates</p>
            <ul className="space-y-1">
              {updates.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => onReadUpdate(n.id)}
                    title="Mark as read"
                    className="w-full rounded-xl bg-orange-soft px-3 py-2 text-left text-[14px] leading-snug transition-colors hover:bg-active"
                  >
                    {n.body}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <nav aria-label="Your errands">
          <p className="mb-2 px-2 text-[13px] font-semibold text-muted">Booked from here</p>
          {errands.length === 0 ? (
            <p className="px-3 text-[14px] text-muted">Nothing yet. Tell me what needs fixing.</p>
          ) : (
            <ul className="space-y-1">
              {errands.map((e) => (
                <li key={e.id} className="rounded-xl px-3 py-2 hover:bg-active">
                  <p className="font-medium leading-tight">{e.title}</p>
                  <p className="text-[13px] font-medium text-good">{e.note}</p>
                </li>
              ))}
            </ul>
          )}
        </nav>

        <Link
          href="/memories"
          className="block rounded-xl px-3 py-2 font-medium hover:bg-active"
        >
          What I remember
        </Link>
      </div>

      <div className="rounded-2xl bg-sheet p-3">
        {plus ? (
          <>
            <p className="font-semibold">
              Errand Plus <Orange />
            </p>
            <p className="text-[13px] text-muted">Unlimited errands, same-day slots.</p>
          </>
        ) : (
          <>
            <p className="font-semibold">
              {used} of {limit} free errands used
            </p>
            <div className="mt-2 flex gap-1" aria-hidden>
              {Array.from({ length: limit }).map((_, i) => (
                <span
                  key={i}
                  className={`h-1.5 flex-1 rounded-full ${i < used ? "bg-orange" : "bg-active"}`}
                />
              ))}
            </div>
            <Link
              href="/plans"
              className="mt-3 inline-block text-[14px] font-semibold underline decoration-orange decoration-2 underline-offset-4"
            >
              {used >= limit ? "Get more errands" : "See Plus"}
            </Link>
          </>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 px-1">
        <div className="min-w-0">
          <p className="truncate font-medium capitalize leading-tight">{session.name}</p>
          <p className="truncate text-[12px] text-muted">{session.email}</p>
        </div>
        <button
          onClick={onSignOut}
          className="shrink-0 rounded-full px-3 py-1.5 text-[13px] font-medium hover:bg-active"
        >
          Log out
        </button>
      </div>
    </div>
  );
}
