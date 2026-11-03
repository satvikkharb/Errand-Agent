import type { ApprovalCard } from "@/lib/api";

const lastAmount = (s: string | undefined) => Number((s ?? "").replace(/[^\d.]/g, "")) || null;

/** Reads an approval card from the backend (approvals.render_card). */
export function parseCard(card: ApprovalCard) {
  const [what = "", when = "", ...rest] = card.lines;
  const isBook = card.action === "book";
  return {
    /** "QuickFix Plumbing (plumber)" */
    what,
    /** "Tue 03 Nov, 09:00 to 11:00 AM" (or "From …" for a reschedule) */
    when,
    /** reschedule only: "To …" */
    to: card.action === "reschedule" ? (rest[0] ?? null) : null,
    /** book: what the user pays, fees included. cancel: the booking's price. reschedule: none. */
    price: isBook ? (card.total ?? lastAmount(rest.at(-1))) : card.action === "cancel" ? lastAmount(rest[0]) : null,
    /** book only: [Service, GST, Service fee]. One entry means no fees, so there is nothing to itemise. */
    breakdown: isBook && (card.breakdown?.length ?? 0) > 1 ? card.breakdown! : [],
  };
}

export const rupees = (n: number) => `₹${n.toLocaleString("en-IN")}`;

/** Stable key for matching a booking across book / cancel / reschedule cards. */
export function bookingKey(card: ApprovalCard) {
  const { what, when } = parseCard(card);
  return `${what}|${when.replace(/^From /, "")}`;
}
