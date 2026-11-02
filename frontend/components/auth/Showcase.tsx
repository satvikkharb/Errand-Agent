// A real-looking slice of the product, shown beside the auth form. Static on purpose.
export function Showcase() {
  return (
    <div
      data-ui
      className="w-full max-w-[420px] space-y-3 text-[15px] leading-snug"
      aria-hidden
    >
      <div className="rise ml-auto max-w-[78%] rounded-2xl rounded-br-sm bg-ink px-4 py-3 text-white">
        Kitchen tap has been dripping since Monday and it&apos;s driving me up the wall
      </div>

      <div className="rise w-fit max-w-[88%] rounded-2xl rounded-bl-sm bg-sheet px-4 py-3 text-ink [animation-delay:200ms]">
        Drips are the worst. I found a plumber who can come tomorrow morning.
      </div>

      <div
        className="rise rounded-2xl bg-sheet p-4 text-ink [animation-delay:400ms]"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="font-semibold">Ravi Menon, plumber</p>
            <p className="text-[13px] text-muted">4.8 from 212 jobs</p>
          </div>
          <p className="text-right font-semibold">
            $45
            <span className="block text-[12px] font-normal text-muted">call-out</span>
          </p>
        </div>
        <p className="mt-3 rounded-lg bg-raised px-3 py-2 text-[14px]">
          Thursday, 10:00 to 12:00 at your place
        </p>
        <div className="mt-3 flex gap-2">
          <span className="rounded-full bg-orange px-4 py-1.5 text-[14px] font-semibold">
            Approve
          </span>
          <span className="rounded-full bg-raised px-4 py-1.5 text-[14px] font-medium">
            Different time
          </span>
        </div>
      </div>

      <p className="pl-1 text-[13px] text-ink/70">Nothing gets booked until you say so.</p>
    </div>
  );
}
