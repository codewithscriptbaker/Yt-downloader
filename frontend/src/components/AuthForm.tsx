"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { BrandLockup } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";

type Mode = "login" | "signup";

type Props = {
  mode: Mode;
};

export function AuthForm({ mode }: Props) {
  const { login, signup, user, ready } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (ready && user) router.replace("/");
  }, [ready, user, router]);

  if (!ready || user) {
    return <div className="auth-page" aria-busy="true" />;
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        if (password.length < 8) {
          setError("Password must be at least 8 characters.");
          setBusy(false);
          return;
        }
        await signup(email.trim(), password, name.trim());
      }
      router.push("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-page__top">
        <Link href="/" className="topbar__brand" aria-label="MediaPort home">
          <BrandLockup size={30} />
        </Link>
        <ThemeToggle />
      </div>

      <main className="auth-page__main">
        <div className="auth-card">
          <h1 className="auth-card__title">
            {mode === "login" ? "Log in" : "Create account"}
          </h1>
          <p className="auth-card__note">
            Optional — you can download without an account. Sign up only to keep
            your download history across devices.
          </p>
          <form onSubmit={(e) => void submit(e)} className="auth-form">
            {mode === "signup" && (
              <label className="auth-field">
                <span>Name</span>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                  placeholder="Optional"
                />
              </label>
            )}
            <label className="auth-field">
              <span>Email</span>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                placeholder="you@example.com"
              />
            </label>
            <label className="auth-field">
              <span>Password</span>
              <input
                type="password"
                required
                minLength={mode === "signup" ? 8 : 1}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                placeholder={
                  mode === "signup" ? "At least 8 characters" : "••••••••"
                }
              />
            </label>
            {error && (
              <p className="form-hint form-hint--error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy
                ? "Please wait…"
                : mode === "login"
                  ? "Log in"
                  : "Sign up"}
            </button>
          </form>
          <p className="auth-switch">
            {mode === "login" ? (
              <>
                No account? <Link href="/signup">Sign up</Link>
              </>
            ) : (
              <>
                Already have an account? <Link href="/login">Log in</Link>
              </>
            )}
          </p>
        </div>
      </main>
    </div>
  );
}
