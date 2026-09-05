/** Empty string = same-origin (Nginx). Local dev sets NEXT_PUBLIC_API_BASE. */
export function getApiBase(): string {
  return (process.env.NEXT_PUBLIC_API_BASE || "").replace(/\/$/, "");
}

export function getWsBase(): string {
  const configured = (process.env.NEXT_PUBLIC_WS_BASE || "").replace(/\/$/, "");
  if (configured) return configured;
  if (typeof window === "undefined") return "";
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}`;
}

export function captchaEnabled(): boolean {
  return (process.env.NEXT_PUBLIC_CAPTCHA_ENABLED || "false").toLowerCase() === "true";
}

export function captchaSiteKey(): string {
  return process.env.NEXT_PUBLIC_CAPTCHA_SITE_KEY || "";
}

/** Turn relative API paths into absolute URLs when API is on another origin. */
export function resolveApiUrl(path: string): string {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  const base = getApiBase();
  if (!base) return path;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}
