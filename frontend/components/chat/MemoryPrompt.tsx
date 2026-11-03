import type { MemoryPrompt } from "@/lib/api";

// "I noticed you've booked mornings 3 times. Want me to remember that?" with Yes/No.
// The backend wrote the question; it also appends it to the reply text, so this only adds the buttons.
export function MemoryPromptCard({
  prompt,
  state,
  working,
  onYes,
  onNo,
}: {
  prompt: MemoryPrompt;
  state: "open" | "yes" | "no";
  working: boolean;
  onYes: () => void;
  onNo: () => void;
}) {
  if (state !== "open") {
    return (
      <p className="rise text-[13px] text-muted">
        {state === "yes" ? "Saved. You can review or forget this under What I remember." : "Okay, I won't remember that."}
      </p>
    );
  }
  return (
    <div className="rise flex flex-wrap gap-2">
      <button
        onClick={onYes}
        disabled={working}
        className="rounded-full bg-ink px-4 py-1.5 text-[14px] font-semibold text-white transition-transform hover:-translate-y-0.5 disabled:opacity-60"
      >
        {prompt.buttons[0] ?? "Yes, remember"}
      </button>
      <button
        onClick={onNo}
        disabled={working}
        className="rounded-full bg-raised px-4 py-1.5 text-[14px] font-medium transition-colors hover:bg-active disabled:opacity-60"
      >
        {prompt.buttons[1] ?? "No"}
      </button>
    </div>
  );
}
