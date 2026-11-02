"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TopBar } from "@/components/TopBar";
import { Field } from "@/components/ui";
import { setPlan, useSession } from "@/lib/session";

const digits = (v: string) => v.replace(/\D/g, "");
const fmtCard = (v: string) => digits(v).slice(0, 16).replace(/(.{4})/g, "$1 ").trim();
const fmtExpiry = (v: string) => {
  const d = digits(v).slice(0, 4);
  return d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
};

export default function CheckoutPage() {
  const router = useRouter();
  const { session, ready } = useSession();
  const [card, setCard] = useState("");
  const [expiry, setExpiry] = useState("");
  const [cvc, setCvc] = useState("");
  const [holder, setHolder] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"form" | "paying" | "paid">("form");

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  if (!ready || !session) return <div className="min-h-screen" />;

  function pay(e: React.FormEvent) {
    e.preventDefault();
    if (!holder.trim()) return setError("Add the name on the card.");
    if (digits(card).length !== 16) return setError("Card numbers have 16 digits.");
    const [mm, yy] = expiry.split("/");
    if (!mm || !yy || yy.length !== 2 || +mm < 1 || +mm > 12) return setError("Use the expiry as MM/YY.");
    if (digits(cvc).length < 3) return setError("The security code has 3 or 4 digits.");

    setError(null);
    setPhase("paying");
    // Mock payment: nothing leaves the browser. Swap in the real processor here.
    setTimeout(() => {
      setPlan("plus");
      setPhase("paid");
    }, 1400);
  }

  if (phase === "paid") {
    return (
      <>
        <TopBar />
        <main className="mx-auto max-w-[560px] px-5 pt-16 sm:px-10">
          <h1 className="rise text-[44px] font-bold leading-[1.04] tracking-tight">
            You&apos;re on Plus.
          </h1>
          <p className="rise mt-4 text-[17px] text-muted [animation-delay:120ms]">
            Unlimited errands and same-day slots are switched on. Go break something (please don&apos;t).
          </p>
          <Link
            href="/chat"
            className="rise mt-8 inline-block rounded-full bg-orange px-7 py-3 font-semibold [animation-delay:240ms]"
          >
            Back to chat
          </Link>
        </main>
      </>
    );
  }

  return (
    <>
      <TopBar back="/plans" backLabel="Back to plans" />
      <main className="mx-auto grid w-full max-w-[920px] gap-10 px-5 pb-20 pt-6 sm:px-10 md:grid-cols-[minmax(0,1fr)_320px]">
        <form onSubmit={pay} noValidate className="space-y-4">
          <h1 className="text-[36px] font-bold leading-[1.05] tracking-tight">Pay for Plus</h1>
          <p className="pb-2 text-muted">Demo checkout: no card is charged and nothing is stored.</p>

          <Field
            label="Name on card"
            autoComplete="cc-name"
            value={holder}
            onChange={(e) => setHolder(e.target.value)}
          />
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
              label="Security code"
              inputMode="numeric"
              autoComplete="cc-csc"
              placeholder="123"
              value={cvc}
              onChange={(e) => setCvc(digits(e.target.value).slice(0, 4))}
            />
          </div>

          {error && (
            <p role="alert" className="text-[14px] font-medium text-bad">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={phase === "paying"}
            className="h-12 w-full rounded-full bg-orange font-semibold transition-transform hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-60 disabled:hover:translate-y-0"
          >
            {phase === "paying" ? "Paying..." : "Pay $9.00"}
          </button>
        </form>

        <aside className="h-fit rounded-3xl bg-sheet p-6">
          <h2 className="text-[18px] font-bold">Your order</h2>
          <dl className="mt-4 space-y-3">
            <div className="flex justify-between gap-4">
              <dt>Errand Plus, monthly</dt>
              <dd className="font-medium">$9.00</dd>
            </div>
            <div className="flex justify-between gap-4 text-muted">
              <dt>Tax</dt>
              <dd>$0.00</dd>
            </div>
            <div className="flex justify-between gap-4 rounded-xl bg-raised px-3 py-2.5 text-[18px] font-bold">
              <dt>Due today</dt>
              <dd>$9.00</dd>
            </div>
          </dl>
          <ul className="mt-6 space-y-1.5 text-[14px] text-muted">
            <li>Unlimited errands</li>
            <li>Same-day slots</li>
            <li>Cancel any time</li>
          </ul>
          <p className="mt-6 text-[13px] text-muted">Signed in as {session.email}</p>
        </aside>
      </main>
    </>
  );
}
