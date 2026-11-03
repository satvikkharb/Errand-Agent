"use client";

import { useState } from "react";
import { rupees } from "@/lib/cards";
import type { AgentOptions, ProviderOption, Slot } from "@/lib/options";

// Service provider | Rating | Available slots, as bubbles. Picking a provider or a slot turns it
// orange; picking a slot also picks its provider. Confirming sends the choice to the agent.

const bubble = (on: boolean, live: boolean) =>
  `rounded-full px-4 py-2 text-[14px] font-medium transition-colors ${
    on ? "bg-orange text-ink" : live ? "bg-sheet hover:bg-orange-soft" : "bg-sheet text-muted"
  }`;

export function SlotPicker({
  options,
  active,
  onConfirm,
}: {
  options: AgentOptions;
  /** Only the latest, unconfirmed picker can be used. */
  active: boolean;
  onConfirm: (provider: ProviderOption, slot: Slot) => void;
}) {
  const [sel, setSel] = useState<{ p: string | null; s: string | null }>({ p: null, s: null });

  const picked = options.picked;
  const p = picked?.providerId ?? sel.p;
  const s = picked?.slotId ?? sel.s;
  const provider = options.matches.find((m) => m.id === p);
  const slot = provider?.slots.find((x) => x.id === s);

  return (
    <div className="rise w-full max-w-[680px] rounded-2xl bg-raised p-3 sm:p-4">
      {options.title && <p className="px-1 pb-3 text-[13px] text-muted">{options.title}</p>}

      <div
        aria-hidden
        className="hidden gap-3 px-1 pb-2 text-[12px] font-medium text-muted sm:grid sm:grid-cols-[minmax(0,1.2fr)_56px_minmax(0,2fr)]"
      >
        <span>Service provider</span>
        <span>Rating</span>
        <span>Available slots</span>
      </div>

      <ul className="space-y-3">
        {options.matches.map((m) => {
          const mine = m.id === p;
          return (
            <li key={m.id} className="grid gap-2 sm:grid-cols-[minmax(0,1.2fr)_56px_minmax(0,2fr)] sm:items-center sm:gap-3">
              <button
                type="button"
                disabled={!active}
                aria-pressed={mine}
                onClick={() => setSel({ p: m.id, s: mine ? sel.s : null })}
                className={`${bubble(mine, active)} !rounded-2xl text-left`}
              >
                <span className="block font-semibold">{m.name}</span>
                <span className={`block text-[13px] font-normal ${mine ? "text-ink/80" : "text-muted"}`}>
                  {rupees(m.price)}
                  <span className="sm:hidden"> · ★ {m.rating.toFixed(1)}</span>
                </span>
              </button>

              <span className="hidden text-[14px] font-semibold sm:block">★ {m.rating.toFixed(1)}</span>

              <div className="flex flex-wrap gap-2" role="group" aria-label={`Slots for ${m.name}`}>
                {m.slots.map((sl) => (
                  <button
                    key={sl.id}
                    type="button"
                    disabled={!active}
                    aria-pressed={mine && sl.id === s}
                    onClick={() => setSel({ p: m.id, s: sl.id })}
                    className={bubble(mine && sl.id === s, active)}
                  >
                    {sl.label}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="mt-4 flex flex-wrap items-center gap-3 px-1">
        {picked ? (
          <p className="text-[14px] text-muted">
            You picked {provider?.name}, {slot?.label}.
          </p>
        ) : active ? (
          provider && slot ? (
            <button
              type="button"
              onClick={() => onConfirm(provider, slot)}
              className="rounded-full bg-ink px-5 py-2 text-[14px] font-semibold text-white transition-transform hover:-translate-y-0.5"
            >
              Book {provider.name}, {slot.label}
            </button>
          ) : (
            <p className="text-[14px] text-muted">Pick a provider and a slot.</p>
          )
        ) : (
          <p className="text-[14px] text-muted">These options are out of date.</p>
        )}
      </div>
    </div>
  );
}
