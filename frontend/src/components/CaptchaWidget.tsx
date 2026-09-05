"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import { captchaEnabled, captchaSiteKey } from "@/lib/config";

declare global {
  interface Window {
    turnstile?: {
      render: (
        el: HTMLElement,
        opts: {
          sitekey: string;
          callback: (token: string) => void;
          "expired-callback"?: () => void;
          "error-callback"?: () => void;
          theme?: "light" | "dark" | "auto";
        },
      ) => string;
      reset: (widgetId?: string) => void;
      remove: (widgetId?: string) => void;
    };
    onTurnstileLoad?: () => void;
  }
}

const SCRIPT_ID = "cf-turnstile-script";

type Props = {
  onToken: (token: string | null) => void;
};

export function CaptchaWidget({ onToken }: Props) {
  const enabled = captchaEnabled();
  const siteKey = captchaSiteKey();
  const hostRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const reactId = useId();

  const renderWidget = useCallback(() => {
    if (!hostRef.current || !window.turnstile || !siteKey) return;
    if (widgetIdRef.current) {
      try {
        window.turnstile.remove(widgetIdRef.current);
      } catch {
        /* ignore */
      }
      widgetIdRef.current = null;
    }
    hostRef.current.innerHTML = "";
    widgetIdRef.current = window.turnstile.render(hostRef.current, {
      sitekey: siteKey,
      theme: "light",
      callback: (token) => onToken(token),
      "expired-callback": () => onToken(null),
      "error-callback": () => onToken(null),
    });
  }, [onToken, siteKey]);

  useEffect(() => {
    if (!enabled || !siteKey) {
      onToken(null);
      return;
    }

    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    if (window.turnstile) {
      renderWidget();
    } else if (existing) {
      window.onTurnstileLoad = () => renderWidget();
    } else {
      window.onTurnstileLoad = () => renderWidget();
      const script = document.createElement("script");
      script.id = SCRIPT_ID;
      script.src =
        "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=onTurnstileLoad";
      script.async = true;
      document.head.appendChild(script);
    }

    return () => {
      if (widgetIdRef.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetIdRef.current);
        } catch {
          /* ignore */
        }
        widgetIdRef.current = null;
      }
    };
  }, [enabled, siteKey, onToken, renderWidget]);

  if (!enabled) return null;

  if (!siteKey) {
    return (
      <p className="form-hint form-hint--warn" role="status">
        CAPTCHA is enabled but NEXT_PUBLIC_CAPTCHA_SITE_KEY is missing.
      </p>
    );
  }

  return (
    <div className="captcha-wrap" data-captcha={reactId}>
      <div ref={hostRef} />
    </div>
  );
}

export function resetCaptcha(): void {
  if (typeof window !== "undefined" && window.turnstile) {
    try {
      window.turnstile.reset();
    } catch {
      /* ignore */
    }
  }
}
