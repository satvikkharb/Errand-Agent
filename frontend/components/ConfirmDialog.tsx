"use client";

import { useEffect } from "react";
import { Orange } from "@/components/Orange";

export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <button aria-label="Stay" className="absolute inset-0 bg-ink/40" onClick={onCancel} />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="leave-title"
        className="rise relative w-full max-w-[380px] rounded-3xl bg-sheet p-6 shadow-xl"
      >
        <h2 id="leave-title" className="text-[24px] font-bold leading-tight tracking-tight">
          Leaving so soon? <Orange />
        </h2>
        <p className="mt-2 text-muted">
          Do you want to log out? Your errands stay safe and will be here when you&apos;re back.
        </p>
        <div className="mt-6 flex gap-2">
          <button
            onClick={onCancel}
            autoFocus
            className="h-11 flex-1 rounded-full bg-orange font-semibold transition-transform hover:-translate-y-0.5"
          >
            Stay
          </button>
          <button
            onClick={onConfirm}
            className="h-11 flex-1 rounded-full bg-raised font-semibold transition-colors hover:bg-active"
          >
            Log out
          </button>
        </div>
      </div>
    </div>
  );
}
