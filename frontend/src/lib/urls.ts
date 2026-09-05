const ALLOWED_HOST_HINTS = [
  "youtube.com",
  "youtu.be",
  "tiktok.com",
  "instagram.com",
  "facebook.com",
  "fb.watch",
  "fb.com",
];

export function looksLikeUrl(raw: string): boolean {
  const value = raw.trim();
  if (value.length < 8 || value.length > 2048) return false;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

export function clientUrlError(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Paste a media URL to continue.";
  if (value.length < 8) return "URL is too short.";
  if (value.length > 2048) return "URL is too long.";
  if (!looksLikeUrl(value)) {
    return "Enter a full URL starting with http:// or https://";
  }
  try {
    const host = new URL(value).hostname.toLowerCase();
    const ok = ALLOWED_HOST_HINTS.some(
      (d) => host === d || host.endsWith(`.${d}`),
    );
    if (!ok) {
      return "Unsupported site. Use YouTube, TikTok, Instagram, or Facebook.";
    }
  } catch {
    return "Invalid URL.";
  }
  return null;
}

export function parseUrlList(raw: string): string[] {
  const parts = raw
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of parts) {
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
  }
  return out;
}
