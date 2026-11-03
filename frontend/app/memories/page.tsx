"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Memory } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Orange } from "@/components/Orange";
import { TopBar } from "@/components/TopBar";

// Maps to GET /memories and POST|DELETE /memories/{id}...: the user can see everything the agent
// remembers, answer its guesses, and make it forget.

const human = (key: string) => key.replace(/^preferred_/, "preferred ").replace(/_/g, " ");
const show = (v: unknown) =>
  typeof v === "boolean" ? (v ? "yes" : "no") : typeof v === "string" || typeof v === "number" ? String(v) : JSON.stringify(v);

export default function MemoriesPage() {
  const router = useRouter();
  const { session, ready } = useSession();
  const userId = session?.userId ?? null;
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  useEffect(() => {
    if (!userId) return;
    let live = true;
    api
      .memories(userId)
      .then((r) => live && setMemories(r.memories))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [userId]);

  if (!ready || !session || !userId) return <div className="min-h-screen" />;

  async function act(m: Memory, action: "confirm" | "reject" | "forget") {
    setBusyId(m.id);
    setError(null);
    try {
      if (action === "confirm") {
        await api.confirmMemory(userId!, m.id);
        setMemories((all) => all?.map((x) => (x.id === m.id ? { ...x, status: "active" } : x)) ?? null);
      } else {
        await (action === "reject" ? api.rejectMemory : api.forgetMemory)(userId!, m.id);
        setMemories((all) => all?.filter((x) => x.id !== m.id) ?? null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusyId(null);
    }
  }

  const guesses = memories?.filter((m) => m.status === "pending_confirmation") ?? [];
  const known = memories?.filter((m) => m.status !== "pending_confirmation") ?? [];

  return (
    <>
      <TopBar />
      <main className="mx-auto w-full max-w-[720px] px-5 pb-20 pt-6 sm:px-10">
        <h1 className="text-[40px] font-bold leading-[1.04] tracking-tight sm:text-[52px]">
          What I remember <Orange />
        </h1>
        <p className="mt-3 max-w-[520px] text-muted">
          Only things you told me, or guesses you said yes to, are ever used. Forget anything and it&apos;s gone.
        </p>

        {error && (
          <p role="alert" className="mt-6 rounded-2xl bg-bad/10 px-4 py-3 text-[14px] text-bad">
            {error}
          </p>
        )}
        {!memories && !error && <p className="mt-8 text-muted">Loading...</p>}

        {guesses.length > 0 && (
          <section className="mt-10">
            <h2 className="text-[20px] font-bold">Guesses waiting for you</h2>
            <ul className="mt-3 space-y-2">
              {guesses.map((m) => (
                <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-orange-soft px-4 py-3">
                  <p>
                    <span className="font-semibold capitalize">{human(m.key)}</span>: {show(m.value)}
                  </p>
                  <span className="flex gap-2">
                    <button
                      disabled={busyId === m.id}
                      onClick={() => void act(m, "confirm")}
                      className="rounded-full bg-ink px-4 py-1.5 text-[14px] font-semibold text-white disabled:opacity-60"
                    >
                      Yes, remember
                    </button>
                    <button
                      disabled={busyId === m.id}
                      onClick={() => void act(m, "reject")}
                      className="rounded-full bg-sheet px-4 py-1.5 text-[14px] font-medium hover:bg-active disabled:opacity-60"
                    >
                      No
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {memories && (
          <section className="mt-10">
            <h2 className="text-[20px] font-bold">Things I know</h2>
            {known.length === 0 ? (
              <p className="mt-3 text-muted">Nothing yet. Book an errand and I&apos;ll start to learn your usuals.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {known.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-3 rounded-2xl bg-sheet px-4 py-3">
                    <p className="min-w-0">
                      <span className="font-semibold capitalize">{human(m.key)}</span>
                      <span className="text-muted">: {show(m.value)}</span>
                    </p>
                    <button
                      disabled={busyId === m.id}
                      onClick={() => void act(m, "forget")}
                      className="shrink-0 rounded-full bg-raised px-4 py-1.5 text-[14px] font-medium hover:bg-active disabled:opacity-60"
                    >
                      Forget
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </main>
    </>
  );
}
