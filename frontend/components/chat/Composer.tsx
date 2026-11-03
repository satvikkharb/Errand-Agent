"use client";

import { useRef, useState } from "react";

export function Composer({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
    if (ref.current) ref.current.style.height = "auto";
  }

  return (
    <div className="px-4 pb-4 pt-2 sm:px-8">
      <div className="mx-auto flex max-w-[720px] items-end gap-2 rounded-3xl bg-raised p-2 pl-5 focus-within:ring-2 focus-within:ring-ink">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder="Describe the problem..."
          aria-label="Message Errand"
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent py-2 outline-none placeholder:text-muted/70"
        />
        <button
          onClick={submit}
          disabled={!value.trim() || disabled}
          aria-label="Send"
          className="grid size-10 shrink-0 place-items-center rounded-full bg-orange transition-transform enabled:hover:-translate-y-0.5 disabled:bg-active disabled:text-muted"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
            <path
              d="M9 15V3m0 0L4 8m5-5 5 5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      <p className="mx-auto mt-2 max-w-[720px] text-center text-[12px] text-muted">
        Enter to send, Shift+Enter for a new line
      </p>
    </div>
  );
}
