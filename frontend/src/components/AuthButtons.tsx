"use client";

import Link from "next/link";
import { useAuth } from "@/components/AuthProvider";

export function AuthButtons() {
  const { user, logout, ready } = useAuth();

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
    <div className="auth-buttons">
      <Link href="/login" className="btn btn--ghost btn--small auth-buttons__login">
        Log in
      </Link>
      <Link
        href="/signup"
        className="btn btn--primary btn--small auth-buttons__signup"
      >
        Sign up
      </Link>
    </div>
  );
}
