/** Peak helpers + URLs for CapCut-style audio/video trim. */

export const MIN_TRIM_SECONDS = 0.25;

export type MediaKind = "audio" | "video";

export function formatTrimTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const frac = seconds - total;
  const tenths = Math.floor(frac * 10);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  if (tenths > 0 && total < 60) {
    return `${m}:${String(s).padStart(2, "0")}.${tenths}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Inline playback URL (browser preview, not forced download). */
export function inlineMediaUrl(downloadUrl: string): string {
  try {
    const u = new URL(downloadUrl, typeof window !== "undefined" ? window.location.origin : "http://local");
    u.searchParams.set("inline", "1");
    return u.toString();
  } catch {
    const join = downloadUrl.includes("?") ? "&" : "?";
    return `${downloadUrl}${join}inline=1`;
  }
}

/**
 * Derive waveform API URL from a signed download URL.
 * `/file?token=&name=` → `/waveform?token=&name=&bars=`
 */
export function waveformUrlFromDownload(downloadUrl: string, bars = 128): string {
  try {
    const u = new URL(downloadUrl, typeof window !== "undefined" ? window.location.origin : "http://local");
    u.pathname = u.pathname.replace(/\/file\/?$/, "/waveform");
    u.searchParams.delete("inline");
    u.searchParams.set("bars", String(Math.max(16, Math.min(bars, 256))));
    return u.toString();
  } catch {
    throw new Error("Invalid download link");
  }
}

export function guessMediaKind(opts: {
  quality?: string | null;
  fileName?: string | null;
}): MediaKind {
  const name = (opts.fileName || "").toLowerCase();
  if (opts.quality === "audio") return "audio";
  if (/\.(mp3|m4a|aac|wav|ogg|opus|flac)$/.test(name)) return "audio";
  if (/\.(mp4|webm|mkv|mov|m4v)$/.test(name)) return "video";
  if (opts.quality && opts.quality !== "audio") return "video";
  return "audio";
}

/** Placeholder bars shown while server peaks load (non-blocking UI). */
export function placeholderPeaks(barCount = 128): Float32Array {
  const n = Math.max(16, Math.min(barCount, 256));
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / Math.max(1, n - 1);
    out[i] = 0.22 + 0.18 * Math.sin(t * Math.PI * 6) ** 2;
  }
  return out;
}

export function isTrimmableMedia(opts: {
  quality?: string | null;
  fileName?: string | null;
}): boolean {
  const name = (opts.fileName || "").toLowerCase();
  if (opts.quality === "audio") return true;
  if (/\.(mp3|m4a|aac|wav|ogg|opus|flac|mp4|webm|mkv|mov|m4v)$/.test(name)) {
    return true;
  }
  // Video quality downloads (best / 720 / 1080…) default to mp4
  if (opts.quality && opts.quality !== "audio") return true;
  return false;
}
