"use client";

import { useState } from "react";
import { AuthModal } from "@/components/AuthModal";
import { useAuth } from "@/components/AuthProvider";

export function AuthButtons() {
  const { user, logout, ready } = useAuth();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"login" | "signup">("login");

  if (!ready) {
    return <span className="auth-buttons auth-buttons--placeholder" aria-hidden />;
  }

  if (user) {
    return (
      <div className="auth-buttons">
        <span className="auth-buttons__email" title={user.email}>
          {user.name || user.email}
        </span>
        <button type="button" className="btn btn--ghost btn--small" onClick={logout}>
          Log out
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="auth-buttons">
        <button
          type="button"
          className="btn btn--ghost btn--small auth-buttons__login"
          onClick={() => {
            setMode("login");
            setOpen(true);
          }}
        >
          Log in
        </button>
        <button
          type="button"
          className="btn btn--primary btn--small auth-buttons__signup"
          onClick={() => {
            setMode("signup");
            setOpen(true);
          }}
        >
          Sign up
        </button>
      </div>
      <AuthModal
        open={open}
        initialMode={mode}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
