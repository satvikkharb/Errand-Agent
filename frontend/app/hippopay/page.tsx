"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ApprovalCard } from "@/lib/api";
import { parseCard, rupees } from "@/lib/cards";
import { recordApproval, setChat, useChat, type CardState } from "@/lib/chat";
import { digits, fmtCard, fmtExpiry, validExpiry } from "@/lib/format";
import { useSession } from "@/lib/session";
import { Field } from "@/components/ui";
import { Graffiti } from "@/components/Graffiti";

// Hippopay: a make-believe payment provider, styled like a hosted checkout. Nothing is charged.
//
// Authorise, then capture: the card is "authorised" when the form is submitted, then the backend
// approval runs the real booking. Only if the booking comes back DONE is the payment captured
// (graffiti). Any other outcome releases the authorisation: no charge, and the chat explains why.

function HippoLogo({ light = false }: { light?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-2 text-[20px] font-bold tracking-tight ${light ? "text-white" : "text-hippo-deep"}`}>
      <span className="grid size-8 place-items-center rounded-full bg-hippo text-[17px]" aria-hidden>
        🦛
      </span>
      hippopay
    </span>
  );
}

type Phase =
  | { kind: "form" }
  | { kind: "paying"; step: "Authorising your card..." | "Booking with the provider..." }
  | { kind: "paid" }
  | { kind: "held"; message: string }
  | { kind: "released"; message: string };

export default function HippopayPage() {
  const router = useRouter();
  const { session, ready } = useSession();
  const { checkout } = useChat();

  const [method, setMethod] = useState<"card" | "upi">("card");
  const [holder, setHolder] = useState("");
  const [card, setCard] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvc, setCvc] = useState("");
  const [upi, setUpi] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "form" });
  // Kept locally: recordApproval() clears `checkout` from the store, and the receipt still needs it.
  const [paidFor, setPaidFor] = useState<ApprovalCard | null>(null);

  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else if (!checkout && !paidFor) router.replace("/chat");
  }, [ready, session, checkout, paidFor, router]);

  const bookingCard = paidFor ?? checkout;
  if (!ready || !session || !bookingCard) return <div className="min-h-screen bg-hippo-deep" />;

  const b = parseCard(bookingCard);
  const amount = b.price !== null ? rupees(b.price) : "";
  const busy = phase.kind === "paying";

  async function pay(e: React.FormEvent) {
    e.preventDefault();
    if (method === "card") {
      if (!holder.trim()) return setError("Add the name on the card.");
      if (digits(card).length !== 16) return setError("Card numbers have 16 digits.");
      if (!validExpiry(expiry)) return setError("Use the expiry as MM/YY.");
      if (digits(cvc).length < 3) return setError("The CVV has 3 or 4 digits.");
    } else if (!/^[\w.-]{2,}@[a-z]{2,}$/i.test(upi.trim())) {
      return setError("That UPI ID should look like name@bank.");
    }
    setError(null);
    const target = bookingCard!;
    setPaidFor(target);

    setPhase({ kind: "paying", step: "Authorising your card..." });
    await new Promise((r) => setTimeout(r, 1100)); // pretend bank round trip
    setPhase({ kind: "paying", step: "Booking with the provider..." });

    try {
      const res = await api.approve(session!.userId, target.approval_id);
      const outcome: CardState = recordApproval(target, res);
      if (outcome === "approved") setPhase({ kind: "paid" });
      else if (outcome === "processing") setPhase({ kind: "held", message: res.reply });
      else setPhase({ kind: "released", message: res.reply });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      // The backend never answered, so the card is still pending in chat and can be retried.
      setChat((s) => ({ ...s, checkout: target }));
      setPhase({ kind: "released", message });
    }
  }

  if (phase.kind === "paid") {
    return (
      <main className="relative grid min-h-screen place-items-center overflow-hidden bg-hippo-deep p-4">
        <Graffiti />
        <div className="rise relative z-10 w-full max-w-[440px] rounded-3xl bg-white p-8 text-center text-hippo-deep">
          <svg width="64" height="64" viewBox="0 0 64 64" className="mx-auto" aria-hidden>
            <circle cx="32" cy="32" r="30" fill="#16c79a" />
            <path d="M19 33l9 9 17-19" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <h1 className="mt-5 font-[family-name:var(--font-marker)] text-[44px] leading-none">
            Payment done!
          </h1>
          <svg viewBox="0 0 240 14" className="mx-auto mt-2 w-48" aria-hidden>
            <path d="M3 9c30-8 60 6 90-1s60-6 90 0 40 2 54-3" stroke="#ff5b14" strokeWidth="4" strokeLinecap="round" fill="none" className="scribble" />
          </svg>
          <p className="mt-4 text-[15px] text-hippo-deep/70">
            {amount} paid. {b.what} is booked for {b.when}.
          </p>
          <button
            onClick={() => router.replace("/chat")}
            className="mt-6 h-12 w-full rounded-full bg-hippo font-semibold text-white transition-transform hover:-translate-y-0.5"
          >
            Back to Errand
          </button>
          <p className="mt-3 text-[12px] text-hippo-deep/50">Hippopay ref HP-{bookingCard.approval_id.slice(0, 8).toUpperCase()}</p>
        </div>
      </main>
    );
  }

  if (phase.kind === "held" || phase.kind === "released") {
    const released = phase.kind === "released";
    return (
      <main className="grid min-h-screen place-items-center bg-hippo-deep p-4">
        <div className="rise w-full max-w-[440px] rounded-3xl bg-white p-8 text-center text-hippo-deep">
          <h1 className="text-[28px] font-bold leading-tight tracking-tight">
            {released ? "No charge made" : "Payment on hold"}
          </h1>
          <p className="mt-3 text-[15px] text-hippo-deep/70">{phase.message}</p>
          <p className="mt-3 text-[13px] text-hippo-deep/50">
            {released
              ? "Your card authorisation was released. Nothing was booked."
              : "We'll capture the payment once the provider confirms, and tell you in the chat."}
          </p>
          <button
            onClick={() => router.replace("/chat")}
            className="mt-6 h-12 w-full rounded-full bg-hippo font-semibold text-white transition-transform hover:-translate-y-0.5"
          >
            Back to Errand
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="grid min-h-screen place-items-center bg-hippo-deep p-4">
      <div className="rise grid w-full max-w-[880px] overflow-hidden rounded-3xl bg-white md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <section className="flex flex-col justify-between gap-10 bg-hippo p-7 text-white sm:p-9">
          <HippoLogo light />
          <div>
            <p className="text-[14px] text-white/75">Paying Errand for</p>
            <p className="mt-1 text-[20px] font-semibold leading-tight">{b.what}</p>
            <p className="mt-1 text-[14px] text-white/75">{b.when}</p>
            <p className="mt-6 text-[48px] font-bold leading-none">{amount}</p>
            {b.note && <p className="mt-2 text-[13px] text-white">{b.note}</p>}
          </div>
          <p className="text-[13px] text-white/75">
            Demo checkout. No real money moves and nothing is stored.
          </p>
        </section>

        <form onSubmit={pay} noValidate className="space-y-4 p-7 sm:p-9">
          <div role="tablist" aria-label="Payment method" className="flex gap-2">
            {(["card", "upi"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={method === m}
                onClick={() => {
                  setMethod(m);
                  setError(null);
                }}
                className={`rounded-full px-4 py-2 text-[14px] font-semibold transition-colors ${
                  method === m ? "bg-hippo-deep text-white" : "bg-hippo-soft text-hippo-deep hover:bg-hippo-soft/60"
                }`}
              >
                {m === "card" ? "Card" : "UPI"}
              </button>
            ))}
          </div>

          {method === "card" ? (
            <>
              <Field label="Name on card" autoComplete="cc-name" value={holder} onChange={(e) => setHolder(e.target.value)} />
              <Field
                label="Card number"
                inputMode="numeric"
                autoComplete="cc-number"
                placeholder="4242 4242 4242 4242"
                value={card}
                onChange={(e) => setCard(fmtCard(e.target.value))}
              />
              <div className="grid grid-cols-2 gap-4">
                <Field
                  label="Expiry"
                  inputMode="numeric"
                  autoComplete="cc-exp"
                  placeholder="MM/YY"
                  value={expiry}
                  onChange={(e) => setExpiry(fmtExpiry(e.target.value))}
                />
                <Field
                  label="CVV"
                  inputMode="numeric"
                  autoComplete="cc-csc"
                  placeholder="123"
                  value={cvc}
                  onChange={(e) => setCvc(digits(e.target.value).slice(0, 4))}
                />
              </div>
            </>
          ) : (
            <Field
              label="UPI ID"
              placeholder="you@bank"
              hint="You'd get a collect request in your UPI app. In this demo it just works."
              value={upi}
              onChange={(e) => setUpi(e.target.value)}
            />
          )}

          {error && (
            <p role="alert" className="text-[14px] font-medium text-bad">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="flex h-12 w-full items-center justify-center gap-2 rounded-full bg-hippo font-semibold text-white transition-transform hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-70 disabled:hover:translate-y-0"
          >
            {busy && (
              <span
                className="size-4 rounded-full border-2 border-white/40 border-t-white"
                style={{ animation: "spin 700ms linear infinite" }}
              />
            )}
            {phase.kind === "paying" ? phase.step : `Pay ${amount}`}
          </button>

          <button
            type="button"
            onClick={() => router.replace("/chat")}
            disabled={busy}
            className="w-full text-center text-[14px] font-medium text-hippo-deep/60 hover:text-hippo-deep"
          >
            Cancel and go back
          </button>
        </form>
      </div>
    </main>
  );
}
