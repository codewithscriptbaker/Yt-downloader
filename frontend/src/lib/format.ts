export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "—";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function formatSizeMb(mb: number | null | undefined): string {
  if (mb == null || Number.isNaN(mb)) return "";
  if (mb < 1) return `${Math.round(mb * 1024)} KB`;
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export function formatExpiresAt(expiresAt: number | null | undefined): string | null {
  if (!expiresAt) return null;
  const ms = expiresAt * 1000 - Date.now();
  if (ms <= 0) return "expired";
  const mins = Math.ceil(ms / 60000);
  if (mins < 60) return `${mins} min`;
  const hours = Math.ceil(mins / 60);
  return `${hours} hr`;
}

export function formatCountdown(expiresAt: number | null | undefined): string | null {
  if (!expiresAt) return null;
  const ms = Math.max(0, expiresAt * 1000 - Date.now());
  if (ms <= 0) return "expired";
  const totalSec = Math.ceil(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function qualityLabel(
  quality: string | null | undefined,
  audioFormat?: string | null,
): string {
  if (!quality) return "";
  if (quality === "audio") return `Audio · ${(audioFormat || "m4a").toUpperCase()}`;
  if (quality === "best") return "Best quality";
  if (/^\d+$/.test(quality)) return `${quality}p`;
  return quality;
}

export function downloadButtonLabel(opts: {
  fileName?: string | null;
  fileSizeMb?: number | null;
  quality?: string | null;
  audioFormat?: string | null;
}): string {
  const parts: string[] = ["Download"];
  const ext =
    opts.fileName?.split(".").pop()?.toUpperCase() ||
    (opts.quality === "audio"
      ? (opts.audioFormat || "m4a").toUpperCase()
      : "MP4");
  parts.push(ext);
  if (opts.fileSizeMb != null) {
    parts.push(formatSizeMb(opts.fileSizeMb));
  }
  return parts.join(" · ");
}

export function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "downloading":
    case "retrying":
      return "Downloading";
    case "processing":
      return "Processing";
    case "done":
      return "Ready";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return status;
  }
}

/** Soft status copy — retries look like a normal download to the user. */
export function statusMessage(
  status: string,
  message: string | null | undefined,
): string {
  if (status === "retrying" || status === "downloading") {
    return "Downloading…";
  }
  if (status === "queued") {
    return message || "Waiting for a free worker…";
  }
  if (status === "processing") {
    return message || "Finalizing file…";
  }
  return message || "Working…";
}

export function softFailureCopy(
  error: string | null | undefined,
  hint: string | null | undefined,
): { title: string; detail: string | null } {
  const title = error || "This download didn’t complete.";
  const detail = hint || hintForUserError(title) ||
    "You can try again with the same link, or pick a different quality.";
  return { title, detail };
}

/** Client-side next-step hints when the API only returns a message string. */
export function hintForUserError(message: string | null | undefined): string | null {
  if (!message) return null;
  const m = message.toLowerCase();
  if (
    m.includes("publicly viewable") ||
    m.includes("private") ||
    m.includes("requires login") ||
    m.includes("friends")
  ) {
    return "Open the link in a private/incognito window. If it asks you to log in, paste a public post instead.";
  }
  if (m.includes("facebook") && (m.includes("couldn’t process") || m.includes("couldn't process") || m.includes("blocked"))) {
    return "Confirm the post plays without logging in, then retry. Public watch/reel links work best.";
  }
  if (m.includes("tiktok")) {
    return "Open the clip in a browser to confirm it’s public, then paste the link again.";
  }
  if (m.includes("couldn't reach") || m.includes("try again")) {
    return "The media site was temporarily unreachable. Try again shortly.";
  }
  return null;
}

export function looksLikeFacebookUrl(raw: string): boolean {
  try {
    const host = new URL(raw.trim()).hostname.toLowerCase();
    return (
      host.includes("facebook.com") ||
      host === "fb.watch" ||
      host.endsWith(".fb.watch") ||
      host === "fb.com" ||
      host.endsWith(".fb.com")
    );
  } catch {
    return /facebook\.com|fb\.watch|fb\.com/i.test(raw);
  }
}
