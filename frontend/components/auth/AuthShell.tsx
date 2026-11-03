import { Logo } from "@/components/Logo";
import { Showcase } from "./Showcase";

export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <section className="flex flex-col justify-between bg-orange p-6 sm:p-10">
        <Logo onOrange />
        <div className="hidden py-12 lg:block">
          <h2 className="mb-8 max-w-[460px] text-[44px] font-bold leading-[1.02] tracking-tight">
            Tell us what&apos;s broken. We&apos;ll send someone.
          </h2>
          <Showcase />
        </div>
        <p className="hidden text-[14px] text-ink/70 lg:block">
          Plumbers, AC servicing and electricians, one chat away.
        </p>
      </section>
      <section className="flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="w-full max-w-[380px]">{children}</div>
      </section>
    </main>
  );
}
