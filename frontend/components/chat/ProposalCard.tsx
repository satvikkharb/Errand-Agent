import Link from "next/link";
import type { ApprovalCard } from "@/lib/api";
import { parseCard, rupees } from "@/lib/cards";
import type { CardState } from "@/lib/chat";

type Props = {
  card: ApprovalCard;
  state: CardState;
  atLimit: boolean;
  working: boolean;
  onApprove: () => void;
  onReject: () => void;
};

const DONE_TEXT: Record<ApprovalCard["action"], string> = {
  book: "Booked. You're all set. 🍊",
  cancel: "Cancelled.",
  reschedule: "Moved. You're all set. 🍊",
};

export function ProposalCard({ card, state, atLimit, working, onApprove, onReject }: Props) {
  const p = parseCard(card);
  const pays = card.action === "book";
  const approved = state === "approved";
  const dim = state === "declined" || state === "stale" || state === "failed";

  return (
    <div
      className={`rise w-full max-w-[420px] rounded-2xl p-4 ${approved ? "bg-good-soft" : "bg-raised"} ${
        dim ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[13px] text-muted">{card.title}</p>
          <p className="text-[17px] font-semibold leading-tight">{p.what}</p>
        </div>
        {p.price !== null && (
          <p className="shrink-0 text-right text-[22px] font-bold leading-none">{rupees(p.price)}</p>
        )}
      </div>

      <p className="mt-3 rounded-lg bg-sheet px-3 py-2 text-[14px] font-medium">{p.when}</p>
      {p.to && <p className="mt-2 rounded-lg bg-sheet px-3 py-2 text-[14px] font-medium">{p.to}</p>}
      {p.note && <p className="mt-2 text-[13px] font-medium text-orange-deep">{p.note}</p>}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {state === "pending" && (
          <>
            {atLimit && pays ? (
              <Link
                href="/plans"
                className="rounded-full bg-ink px-5 py-2 text-[14px] font-semibold text-white"
              >
                Upgrade to book this
              </Link>
            ) : (
              <button
                onClick={onApprove}
                disabled={working}
                className="rounded-full bg-orange px-5 py-2 text-[14px] font-semibold transition-transform hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-60 disabled:hover:translate-y-0"
              >
                {working ? "One sec..." : pays ? "Approve and pay" : "Approve"}
              </button>
            )}
            <button
              onClick={onReject}
              disabled={working}
              className="rounded-full bg-sheet px-5 py-2 text-[14px] font-medium transition-colors hover:bg-active disabled:opacity-60"
            >
              Not this one
            </button>
          </>
        )}
        {approved && <p className="text-[14px] font-semibold text-good">{DONE_TEXT[card.action]}</p>}
        {state === "processing" && (
          <p className="text-[14px] font-medium text-muted">Confirming with the provider. We&apos;ll update you here.</p>
        )}
        {state === "declined" && <p className="text-[14px] text-muted">Passed on this one.</p>}
        {state === "stale" && <p className="text-[14px] text-muted">Replaced by a newer option.</p>}
        {state === "failed" && <p className="text-[14px] text-muted">This didn&apos;t go through. Nothing was booked.</p>}
      </div>
    </div>
  );
}
