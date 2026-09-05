"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { Logo } from "@/components/Logo";

type Mode = "login" | "signup";

type Props = {
  open: boolean;
  initialMode?: Mode;
  onClose: () => void;
};

export function AuthModal({ open, initialMode = "login", onClose }: Props) {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setMode(initialMode);
      setError(null);
    }
  }, [open, initialMode]);

  if (!open) return null;

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
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-overlay" role="dialog" aria-modal="true">
      <button
        type="button"
        className="auth-overlay__backdrop"
        aria-label="Close"
        onClick={onClose}
      />
      <div className="auth-modal">
        <div className="auth-modal__head">
          <div className="auth-modal__brand">
            <Logo size={28} />
            <h2>{mode === "login" ? "Log in" : "Create account"}</h2>
          </div>
          <button type="button" className="btn btn--ghost btn--small" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="auth-modal__note">
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
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
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
              No account?{" "}
              <button type="button" className="text-link" onClick={() => setMode("signup")}>
                Sign up
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button type="button" className="text-link" onClick={() => setMode("login")}>
                Log in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
