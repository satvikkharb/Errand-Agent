import Link from "next/link";
import type { Proposal } from "@/lib/agent";

import type { ProposalState } from "@/lib/chat";

type Props = {
  proposal: Proposal;
  state: ProposalState;
  atLimit: boolean;
  onApprove: () => void;
  onChange: () => void;
};

export function ProposalCard({ proposal: p, state, atLimit, onApprove, onChange }: Props) {
  const done = state === "approved";
  return (
    <div
      className={`rise w-full max-w-[420px] rounded-2xl p-4 ${
        done ? "bg-good-soft" : "bg-raised"
      } ${state === "declined" ? "opacity-60" : ""}`}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[13px] text-muted">{p.trade}</p>
          <p className="text-[17px] font-semibold leading-tight">{p.provider}</p>
          <p className="text-[13px] text-muted">
            {p.rating.toFixed(1)} from {p.jobs} jobs
          </p>
        </div>
        <p className="text-right text-[22px] font-bold leading-none">
          ${p.price}
          <span className="mt-1 block max-w-[110px] text-[12px] font-normal leading-tight text-muted">
            {p.priceNote}
          </span>
        </p>
      </div>

      <p className="mt-3 rounded-lg bg-sheet px-3 py-2 text-[14px] font-medium">{p.when}</p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {state === "pending" && (
          <>
            {atLimit ? (
              <Link
                href="/plans"
                className="rounded-full bg-ink px-5 py-2 text-[14px] font-semibold text-white"
              >
                Upgrade to book this
              </Link>
            ) : (
              <button
                onClick={onApprove}
                className="rounded-full bg-orange px-5 py-2 text-[14px] font-semibold transition-transform hover:-translate-y-0.5 active:translate-y-0"
              >
                Approve and pay
              </button>
            )}
            <button
              onClick={onChange}
              className="rounded-full bg-sheet px-5 py-2 text-[14px] font-medium transition-colors hover:bg-active"
            >
              Different time
            </button>
          </>
        )}
        {done && <p className="text-[14px] font-semibold text-good">Booked. You&apos;re all set. 🍊</p>}
        {state === "declined" && <p className="text-[14px] text-muted">Passed on this one.</p>}
      </div>
    </div>
  );
}
