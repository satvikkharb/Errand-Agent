import type { ApprovalCard } from "@/lib/api";

/** Reads the display lines the backend puts on an approval card (approvals.render_card). */
export function parseCard(card: ApprovalCard) {
  const [what = "", when = "", third = "", note = ""] = card.lines;
  const price = card.action === "reschedule" ? null : Number(third.replace(/[^\d.]/g, "")) || null;
  return {
    /** "Ravi Kumar Plumber (plumber)" */
    what,
    /** "Tue 03 Nov, 10:00 AM" (or "From …" for a reschedule) */
    when,
    /** reschedule only: "To …" */
    to: card.action === "reschedule" ? third : null,
    price,
    /** "Price changed from ₹x to ₹y" */
    note: card.action === "book" ? note : "",
  };
}

export const rupees = (n: number) => `₹${n.toLocaleString("en-IN")}`;

/** Stable key for matching a booking across book / cancel / reschedule cards. */
export function bookingKey(card: ApprovalCard) {
  const { what, when } = parseCard(card);
  return `${what}|${when.replace(/^From /, "")}`;
}
