"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { TopBar } from "@/components/TopBar";
import { useSession } from "@/lib/session";

const FREE = ["3 errands a month", "Next-available slots", "Approve every booking"];
const PLUS = [
  "Unlimited errands",
  "Same-day slots",
  "Your favourite providers first",
  "Rebook a past errand in one tap",
];

function Tick() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" className="mt-0.5 shrink-0" aria-hidden>
      <path d="M4 9.500l3.200 3.200L14 5.500" stroke="currentColor" strokeWidth="2.200" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function PlansPage() {
  const router = useRouter();
  const { session, ready } = useSession();

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  if (!ready || !session) return <div className="min-h-screen" />;
  const plus = session.plan === "plus";

  return (
    <>
      <TopBar />
      <main className="mx-auto w-full max-w-[920px] px-5 pb-20 pt-8 sm:px-10">
        <h1 className="max-w-[640px] text-[40px] font-bold leading-[1.04] tracking-tight sm:text-[56px]">
          Fix more, wait less.
        </h1>
        <p className="mt-4 max-w-[520px] text-[17px] text-muted">
          Start free. Move to Plus when your home starts asking for more than three favours a month.
        </p>

        <div className="mt-12 grid gap-4 md:grid-cols-2">
          <section className="flex flex-col rounded-3xl bg-sheet p-7">
            <h2 className="text-[20px] font-bold">Pay as you go</h2>
            <p className="mt-4 text-[48px] font-bold leading-none">
              $0<span className="ml-1 text-[16px] font-medium text-muted">/ month</span>
            </p>
            <ul className="mt-6 flex-1 space-y-3">
              {FREE.map((f) => (
                <li key={f} className="flex gap-3">
                  <Tick />
                  {f}
                </li>
              ))}
            </ul>
            <p className="mt-8 rounded-full bg-raised py-3 text-center font-semibold text-muted">
              {plus ? "Included" : "Your current plan"}
            </p>
          </section>

          <section className="flex flex-col rounded-3xl bg-orange p-7">
            <h2 className="text-[20px] font-bold">Errand Plus</h2>
            <p className="mt-4 text-[48px] font-bold leading-none">
              $9<span className="ml-1 text-[16px] font-medium text-ink/70">/ month</span>
            </p>
            <ul className="mt-6 flex-1 space-y-3">
              {PLUS.map((f) => (
                <li key={f} className="flex gap-3">
                  <Tick />
                  {f}
                </li>
              ))}
            </ul>
            {plus ? (
              <p className="mt-8 rounded-full bg-ink/10 py-3 text-center font-semibold">
                Your current plan
              </p>
            ) : (
              <Link
                href="/checkout"
                className="mt-8 rounded-full bg-ink py-3 text-center font-semibold text-white transition-transform hover:-translate-y-0.5"
              >
                Get Plus
              </Link>
            )}
          </section>
        </div>

        <p className="mt-8 text-[14px] text-muted">
          Cancel any time. Provider fees such as call-outs and parts are billed separately and always
          shown before you approve.
        </p>
      </main>
    </>
  );
}
