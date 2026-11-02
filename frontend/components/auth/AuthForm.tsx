"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { signIn, useSession } from "@/lib/session";
import { Orange } from "@/components/Orange";
import { Field } from "@/components/ui";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const isSignup = mode === "signup";
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { session } = useSession();

  // Signed-in people have no business on the auth screens.
  useEffect(() => {
    if (session) router.replace("/chat");
  }, [session, router]);

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const email = String(f.get("email") ?? "").trim();
    const password = String(f.get("password") ?? "");
    const name = String(f.get("name") ?? "").trim();

    if (isSignup && !name) return setError("What should we call you?");
    if (!/^\S+@\S+\.\S+$/.test(email)) return setError("That email doesn't look right.");
    if (password.length < 8) return setError("Passwords need at least 8 characters.");

    setError(null);
    setBusy(true);
    // Mock auth. Replace with the backend call later.
    setTimeout(() => {
      signIn({
        name:
          name ||
          email
            .split("@")[0]
            .split(/[._-]+/)
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(" "),
        email,
        plan: "free",
      });
    }, 500);
  }

  return (
    <>
      <h1 className="text-[34px] font-bold leading-[1.05] tracking-tight">
        {isSignup ? "Let's get you set up" : "Welcome back"} <Orange />
      </h1>
      <p className="mt-2 text-muted">
        {isSignup
          ? "Three free errands a month. No card needed."
          : "Pick up where your last errand left off."}
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-4" noValidate>
        {isSignup && (
          <Field label="First name" name="name" autoComplete="given-name" placeholder="Satvik" />
        )}
        <Field
          label="Email"
          name="email"
          type="email"
          autoComplete="email"
          placeholder="you@home.com"
        />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete={isSignup ? "new-password" : "current-password"}
          placeholder="At least 8 characters"
        />

        {error && (
          <p role="alert" className="text-[14px] font-medium text-bad">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="h-12 w-full rounded-full bg-orange font-semibold transition-transform hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-60 disabled:hover:translate-y-0"
        >
          {busy ? "One sec..." : isSignup ? "Create account" : "Log in"}
        </button>
      </form>

      <p className="mt-6 text-[14px] text-muted">
        {isSignup ? "Already have an account? " : "New here? "}
        <Link
          href={isSignup ? "/login" : "/signup"}
          className="font-semibold text-ink underline decoration-orange decoration-2 underline-offset-4"
        >
          {isSignup ? "Log in" : "Create one"}
        </Link>
      </p>
    </>
  );
}
