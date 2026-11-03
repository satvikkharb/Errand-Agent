import Link from "next/link";
import { Logo } from "@/components/Logo";

export function TopBar({ back = "/chat", backLabel = "Back to chat" }: { back?: string; backLabel?: string }) {
  return (
    <header className="flex h-16 items-center justify-between px-5 sm:px-10">
      <Logo href="/chat" />
      <Link href={back} className="rounded-full px-4 py-2 text-[14px] font-medium hover:bg-active">
        {backLabel}
      </Link>
    </header>
  );
}
