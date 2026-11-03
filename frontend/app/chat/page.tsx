"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type ApprovalCard, type Notification } from "@/lib/api";
import {
  addItem,
  beginCheckout,
  ensureUser,
  recordApproval,
  recordChat,
  recordReject,
  forgetSession,
  resetChat,
  setCardState,
  setMemoryState,
  setPicked,
  startNewErrand,
  useChat,
} from "@/lib/chat";
import { extractOptions, splitAroundList } from "@/lib/options";
import { signOut, useSession } from "@/lib/session";
import { SlotPicker } from "@/components/chat/SlotPicker";
import { Sidebar } from "@/components/chat/Sidebar";
import { ProposalCard } from "@/components/chat/ProposalCard";
import { MemoryPromptCard } from "@/components/chat/MemoryPrompt";
import { TraceDialog } from "@/components/chat/TraceDialog";
import { Composer } from "@/components/chat/Composer";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Orange } from "@/components/Orange";

const FREE_LIMIT = 3;

// Written to match the seeded providers (Indiranagar; plumbers, electricians, AC servicing).
const STARTERS = [
  "Find a plumber in Indiranagar for my kitchen tap tomorrow morning, under ₹1500",
  "My AC in Koramangala needs servicing next week",
  "Fan regulator is broken, I need an electrician in Indiranagar",
];

const AFFIRM = new Set(["yes", "y", "yep", "ok", "okay", "sure", "approve", "confirm", "book it", "go ahead", "do it", "yes please"]);
const REJECT = new Set(["no", "n", "nope", "reject", "not this one"]);
const norm = (s: string) => s.toLowerCase().replace(/[^\w\s]/g, "").trim();

const errText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong.");

export default function ChatPage() {
  const router = useRouter();
  const { session, ready } = useSession();
  const chat = useChat();

  const [thinking, setThinking] = useState(false);
  const [working, setWorking] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const [updates, setUpdates] = useState<Notification[]>([]);
  const signedIn = !!session;
  const userId = session?.userId ?? null;
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  // Never show one person's thread to another.
  useEffect(() => {
    if (userId) ensureUser(userId);
  }, [userId]);

  // Browser back from chat asks first instead of silently dropping the user on /login.
  useEffect(() => {
    if (!signedIn) return;
    history.pushState({ errandGuard: true }, "", location.href);
    const onPop = () => {
      history.pushState({ errandGuard: true }, "", location.href);
      setLeaving(true);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [signedIn]);

  // After a reload, a card in the thread may have expired or been replaced: ask the backend.
  const mine = chat.userId === userId;
  const sessionId = mine ? chat.sessionId : null;
  useEffect(() => {
    if (!userId || !sessionId) return;
    let live = true;
    api
      .session(userId, sessionId)
      .then((r) => {
        if (!live) return;
        for (const i of chat.items) {
          if (i.kind === "card" && i.state === "pending" && r.approval_card?.approval_id !== i.card.approval_id) {
            setCardState(i.card.approval_id, "stale");
          }
        }
      })
      .catch((e) => {
        if (live && e instanceof ApiError && e.status === 404) forgetSession();
      });
    return () => {
      live = false;
    };
    // Only on (re)entering a session; later changes arrive through the chat store itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, sessionId]);

  // Things that happened while the user was away (provider cancelled, a slot freed up...).
  useEffect(() => {
    if (!userId) return;
    let live = true;
    const poll = () =>
      api
        .notifications(userId, true)
        .then((r) => live && setUpdates(r.notifications))
        .catch(() => {});
    void poll();
    const t = setInterval(poll, 30000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [userId]);

  const items = mine ? chat.items : [];
  const errands = mine ? chat.errands : [];

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items.length, thinking]);

  const closeTrace = useCallback(() => setTrace(null), []);

  const readUpdate = useCallback(
    (id: string) => {
      if (!userId) return;
      setUpdates((u) => u.filter((n) => n.id !== id));
      void api.readNotification(userId, id).catch(() => {});
    },
    [userId],
  );

  if (!ready || !session) return <div className="min-h-screen bg-ground" />;

  const plus = session.plan === "plus";
  const atLimit = !plus && errands.length >= FREE_LIMIT;
  const first = session.name.split(" ")[0];
  const pending = [...items].reverse().find((i) => i.kind === "card" && i.state === "pending");
  // Only the newest picker is live; older ones are history.
  const lastPickerId = [...items].reverse().find((i) => i.kind === "agent" && i.options)?.id;

  async function approve(card: ApprovalCard) {
    if (card.action === "book") {
      // Booking is paid for first: Hippopay captures, then the backend approval runs the booking.
      beginCheckout(card);
      router.push("/hippopay");
      return;
    }
    setWorking(card.approval_id);
    try {
      recordApproval(card, await api.approve(session!.userId, card.approval_id));
    } catch (e) {
      addItem({ kind: "error", text: errText(e) });
    } finally {
      setWorking(null);
    }
  }

  async function reject(card: ApprovalCard) {
    setWorking(card.approval_id);
    try {
      recordReject(card, await api.reject(session!.userId, card.approval_id));
    } catch (e) {
      addItem({ kind: "error", text: errText(e) });
    } finally {
      setWorking(null);
    }
  }

  async function answerMemory(memoryId: string, yes: boolean) {
    setWorking(memoryId);
    try {
      await (yes ? api.confirmMemory : api.rejectMemory)(session!.userId, memoryId);
      setMemoryState(memoryId, yes ? "yes" : "no");
    } catch (e) {
      addItem({ kind: "error", text: errText(e) });
    } finally {
      setWorking(null);
    }
  }

  async function send(text: string) {
    addItem({ kind: "user", text });

    // A bare yes/no answers the open card, exactly like the backend does. Booking still goes via payment.
    if (pending?.kind === "card") {
      const n = norm(text);
      if (AFFIRM.has(n)) {
        if (atLimit && pending.card.action === "book") {
          addItem({ kind: "notice", text: "You've used your free errands. Upgrade to book this one." });
          return;
        }
        return void approve(pending.card);
      }
      if (REJECT.has(n)) return void reject(pending.card);
    }

    setThinking(true);
    try {
      let res = await api.chat(session!.userId, text, chat.sessionId);
      if (res.exit_reason === "not_found" && chat.sessionId) {
        // The backend no longer knows this conversation (it restarted, or the data was reset).
        // Drop the stale id and old cards, and send the same message into a fresh conversation.
        forgetSession();
        res = await api.chat(session!.userId, text, null);
      }
      // "Show me slots" comes back as prose. The search behind it is in the run trace, structured:
      // fetch it so the thread can show a real provider / rating / slots picker.
      const options =
        res.run_id && !res.approval_card
          ? await api
              .trace(res.run_id)
              .then((t) => extractOptions(t.spans))
              .catch(() => null)
          : null;
      recordChat(res, options);
    } catch (e) {
      addItem({ kind: "error", text: errText(e) });
    } finally {
      setThinking(false);
    }
  }

  function logOut() {
    // Cache Components keeps this page alive in the background, so clear everything that could
    // greet the next login: the dialog, the thread and the previous user's errands.
    setLeaving(false);
    resetChat();
    signOut();
    router.replace("/login");
  }

  const sidebar = (
    <Sidebar
      session={session}
      errands={errands}
      updates={updates}
      used={Math.min(errands.length, FREE_LIMIT)}
      limit={FREE_LIMIT}
      onNew={() => {
        startNewErrand();
        setDrawer(false);
      }}
      onSignOut={() => setLeaving(true)}
      onReadUpdate={readUpdate}
    />
  );

  return (
    <div className="flex h-dvh bg-ground">
      <aside className="hidden w-[272px] shrink-0 md:block">{sidebar}</aside>

      {drawer && (
        <div className="fixed inset-0 z-30 md:hidden">
          <button
            aria-label="Close menu"
            className="absolute inset-0 bg-ink/40"
            onClick={() => setDrawer(false)}
          />
          <aside className="rise absolute inset-y-0 left-0 w-[290px] bg-ground">{sidebar}</aside>
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col bg-sheet md:m-2 md:ml-0 md:rounded-2xl md:ring-1 md:ring-line">
        <header className="flex h-14 shrink-0 items-center gap-3 px-4 sm:px-6">
          <button
            onClick={() => setDrawer(true)}
            aria-label="Open menu"
            className="grid size-9 place-items-center rounded-full hover:bg-active md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
              <path d="M3 5h12M3 9h12M3 13h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          <p className="font-semibold">{items.length ? "New errand" : "Errand"}</p>
          <span className="ml-auto rounded-full bg-raised px-3 py-1 text-[13px] font-medium">
            {plus ? "Plus" : "Free plan"}
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 sm:px-8">
          <div className="mx-auto flex min-h-full max-w-[720px] flex-col">
            {items.length === 0 ? (
              <div className="rise my-auto py-10">
                <h1 className="text-[40px] font-bold leading-[1.05] tracking-tight sm:text-[52px]">
                  Hi {first}. What needs{" "}
                  <span className="whitespace-nowrap">
                    fixing? <Orange />
                  </span>
                </h1>
                <p className="mt-3 max-w-[480px] text-muted">
                  Describe it like you would to a friend. I&apos;ll find someone good and check with
                  you before anything gets booked.
                </p>
                <div className="mt-8 flex flex-wrap gap-2">
                  {STARTERS.map((s) => (
                    <button
                      key={s}
                      onClick={() => void send(s)}
                      className="rounded-full bg-raised px-4 py-2 text-left text-[14px] font-medium transition-colors hover:bg-orange-soft"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <ol className="space-y-3 py-6">
                {items.map((m) => (
                  <li key={m.id} className={m.kind === "user" ? "flex justify-end" : ""}>
                    {m.kind === "user" && (
                      <p className="rise max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-white">
                        {m.text}
                      </p>
                    )}
                    {m.kind === "agent" && (
                      <div className="rise space-y-2">
                        {(() => {
                          // With a picker, the model's numbered list is redundant: show its intro, the picker, then its question.
                          const { before, after } = m.options ? splitAroundList(m.text) : { before: m.text, after: "" };
                          const bubble = (t: string) => (
                            <p className="w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-raised px-4 py-2.5">
                              {t}
                            </p>
                          );
                          return (
                            <>
                              {before && bubble(before)}
                              {m.options && (
                                <SlotPicker
                                  options={m.options}
                                  active={m.id === lastPickerId && !m.options.picked && !thinking}
                                  onConfirm={(p, s) => {
                                    setPicked(m.id, { providerId: p.id, slotId: s.id });
                                    void send(`Book ${p.name} on ${s.label}`);
                                  }}
                                />
                              )}
                              {after && bubble(after)}
                            </>
                          );
                        })()}
                        {m.runId && (
                          <button
                            onClick={() => setTrace(m.runId!)}
                            className="mt-1 pl-2 text-[12px] text-muted underline decoration-line underline-offset-2 hover:text-ink"
                          >
                            How I got there
                          </button>
                        )}
                      </div>
                    )}
                    {m.kind === "notice" && (
                      <p className="rise w-fit max-w-[85%] rounded-2xl bg-orange-soft px-4 py-2.5 text-[14px]">
                        {m.text}
                      </p>
                    )}
                    {m.kind === "error" && (
                      <p role="alert" className="rise w-fit max-w-[85%] rounded-2xl bg-bad/10 px-4 py-2.5 text-[14px] text-bad">
                        {m.text}
                      </p>
                    )}
                    {m.kind === "card" && (
                      <ProposalCard
                        card={m.card}
                        state={m.state}
                        atLimit={atLimit}
                        working={working === m.card.approval_id}
                        onApprove={() => void approve(m.card)}
                        onReject={() => void reject(m.card)}
                      />
                    )}
                    {m.kind === "memory" && (
                      <MemoryPromptCard
                        prompt={m.prompt}
                        state={m.state}
                        working={working === m.prompt.memory_id}
                        onYes={() => void answerMemory(m.prompt.memory_id, true)}
                        onNo={() => void answerMemory(m.prompt.memory_id, false)}
                      />
                    )}
                  </li>
                ))}
                {thinking && (
                  <li aria-label="Errand is typing">
                    <span className="inline-flex gap-1 rounded-2xl rounded-bl-sm bg-raised px-4 py-3.5">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className="blip size-1.5 rounded-full bg-ink"
                          style={{ animationDelay: `${i * 150}ms` }}
                        />
                      ))}
                    </span>
                  </li>
                )}
              </ol>
            )}
            <div ref={endRef} />
          </div>
        </div>

        <Composer onSend={(t) => void send(t)} disabled={thinking} />
      </main>

      <ConfirmDialog open={leaving} onCancel={() => setLeaving(false)} onConfirm={logOut} />
      {trace && <TraceDialog runId={trace} onClose={closeTrace} />}
    </div>
  );
}
