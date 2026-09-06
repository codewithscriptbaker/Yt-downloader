const ALLOWED_HOST_HINTS = [
  "youtube.com",
  "youtu.be",
  "tiktok.com",
  "instagram.com",
  "facebook.com",
  "fb.watch",
  "fb.com",
];

export type MediaPlatform =
  | "youtube"
  | "tiktok"
  | "instagram"
  | "facebook";

const PLATFORM_LABEL: Record<MediaPlatform, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  facebook: "Facebook",
};

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

/** Detect which supported platform a URL belongs to, or null if unknown. */
export function detectPlatform(raw: string): MediaPlatform | null {
  try {
    const host = new URL(raw.trim()).hostname.toLowerCase();
    if (
      host === "youtu.be" ||
      host.endsWith(".youtu.be") ||
      host === "youtube.com" ||
      host.endsWith(".youtube.com")
    ) {
      return "youtube";
    }
    if (host === "tiktok.com" || host.endsWith(".tiktok.com")) {
      return "tiktok";
    }
    if (host === "instagram.com" || host.endsWith(".instagram.com")) {
      return "instagram";
    }
    if (
      host === "facebook.com" ||
      host.endsWith(".facebook.com") ||
      host === "fb.watch" ||
      host.endsWith(".fb.watch") ||
      host === "fb.com" ||
      host.endsWith(".fb.com")
    ) {
      return "facebook";
    }
  } catch {
    /* not a parseable URL */
  }
  return null;
}

export function platformLabel(platform: MediaPlatform): string {
  return PLATFORM_LABEL[platform];
}

/**
 * Short confidence line under the paste box, e.g. "YouTube link recognized"
 * or "2 links · YouTube · TikTok".
 */
export function platformHintForInput(raw: string): string | null {
  const list = parseUrlList(raw).filter((u) => looksLikeUrl(u));
  if (!list.length) return null;

  const platforms = list
    .map(detectPlatform)
    .filter((p): p is MediaPlatform => p != null);

  if (!platforms.length) return null;

  const unique = [...new Set(platforms)];
  const names = unique.map(platformLabel);

  if (list.length === 1) {
    return `${names[0]} link recognized`;
  }

  if (unique.length === 1) {
    return `${list.length} ${names[0]} links recognized`;
  }

  return `${list.length} links · ${names.join(" · ")}`;
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
