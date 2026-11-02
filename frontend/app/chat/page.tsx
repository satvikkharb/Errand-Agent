"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { askAgent, type Message } from "@/lib/agent";
import { signOut, useSession } from "@/lib/session";
import { Sidebar, type Errand } from "@/components/chat/Sidebar";
import { ProposalCard, type ProposalState } from "@/components/chat/ProposalCard";
import { Composer } from "@/components/chat/Composer";

const FREE_LIMIT = 3;

const SEED_ERRANDS: Errand[] = [
  { id: "s1", title: "Socket repair", note: "Booked for Thursday, 10:00" },
  { id: "s2", title: "AC service", note: "Done, 12 Oct", done: true },
  { id: "s3", title: "Kitchen sink unblocked", note: "Done, 3 Sep", done: true },
];

const STARTERS = [
  "My kitchen tap won't stop dripping",
  "AC is blowing warm air",
  "A socket sparks when I plug things in",
];

const uid = () => Math.random().toString(36).slice(2, 9);

export default function ChatPage() {
  const router = useRouter();
  const { session, ready } = useSession();

  const [messages, setMessages] = useState<Message[]>([]);
  const [states, setStates] = useState<Record<string, ProposalState>>({});
  const [errands, setErrands] = useState<Errand[]>(SEED_ERRANDS);
  const [thinking, setThinking] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, thinking]);

  if (!ready || !session) return <div className="min-h-screen bg-ground" />;

  const plus = session.plan === "plus";
  const used = errands.filter((e) => !e.done).length; // errands booked and not yet done count against the month
  const atLimit = !plus && used >= FREE_LIMIT;
  const first = session.name.split(" ")[0];

  const pending = [...messages]
    .reverse()
    .find((m) => m.role === "agent" && m.proposal && (states[m.proposal.id] ?? "pending") === "pending");

  function approve(m: Message) {
    if (m.role !== "agent" || !m.proposal) return;
    const p = m.proposal;
    setStates((s) => ({ ...s, [p.id]: "approved" }));
    setErrands((e) => [
      { id: uid(), title: `${p.trade}, ${p.provider.split(",")[0]}`, note: `Booked for ${p.when.split(",")[0].replace("Tomorrow", "tomorrow")}` },
      ...e,
    ]);
    setMessages((ms) => [
      ...ms,
      {
        id: uid(),
        role: "agent",
        text: `Done! ${p.provider.split(" ")[0]} is booked for ${p.when.toLowerCase()}. You'll get a nudge an hour before they arrive.`,
      },
    ]);
  }

  async function send(text: string) {
    const history = [...messages, { id: uid(), role: "user", text } as Message];
    setMessages(history);

    if (pending && !atLimit && /^(yes|yep|ok|okay|approve|book it|go ahead|sure)\b/i.test(text)) {
      approve(pending);
      return;
    }

    setThinking(true);
    try {
      const reply = await askAgent(text, history);
      if (/(different|another|later|other time|reschedule)/i.test(text) && pending?.role === "agent") {
        const id = pending.proposal!.id;
        setStates((s) => ({ ...s, [id]: "declined" }));
      }
      setMessages((ms) => [...ms, ...reply]);
    } finally {
      setThinking(false);
    }
  }

  function reschedule(m: Message) {
    if (m.role !== "agent" || !m.proposal) return;
    const id = m.proposal.id;
    setStates((s) => ({ ...s, [id]: "declined" }));
    void send("Can we try a different time?");
  }

  function newErrand() {
    setMessages([]);
    setStates({});
    setDrawer(false);
  }

  function logOut() {
    signOut();
    router.replace("/login");
  }

  const sidebar = (
    <Sidebar
      session={session}
      errands={errands}
      used={Math.min(used, FREE_LIMIT)}
      limit={FREE_LIMIT}
      onNew={newErrand}
      onSignOut={logOut}
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
          <p className="font-semibold">{messages.length ? "New errand" : "Errand"}</p>
          <span className="ml-auto rounded-full bg-raised px-3 py-1 text-[13px] font-medium">
            {plus ? "Plus" : "Free plan"}
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 sm:px-8">
          <div className="mx-auto flex min-h-full max-w-[720px] flex-col">
            {messages.length === 0 ? (
              <div className="rise my-auto py-10">
                <h1 className="text-[40px] font-bold leading-[1.05] tracking-tight sm:text-[52px]">
                  Hi {first}. What needs fixing?
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
                      className="rounded-full bg-raised px-4 py-2 text-[14px] font-medium transition-colors hover:bg-orange-soft"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <ol className="space-y-3 py-6">
                {messages.map((m) => (
                  <li key={m.id} className={m.role === "user" ? "flex justify-end" : ""}>
                    {m.role === "user" ? (
                      <p className="rise max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-white">
                        {m.text}
                      </p>
                    ) : m.proposal ? (
                      <ProposalCard
                        proposal={m.proposal}
                        state={states[m.proposal.id] ?? "pending"}
                        atLimit={atLimit}
                        onApprove={() => approve(m)}
                        onChange={() => reschedule(m)}
                      />
                    ) : (
                      <p className="rise w-fit max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-raised px-4 py-2.5">
                        {m.text}
                      </p>
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
    </div>
  );
}
