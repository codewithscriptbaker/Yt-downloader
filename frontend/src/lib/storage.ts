import type { AudioFormat, JobStatus } from "./types";

const JOBS_KEY = "mediaport-active-jobs";
const HISTORY_KEY = "mediaport-history";
const PREFS_KEY = "mediaport-prefs";

export type StoredJob = {
  job_id: string;
  source_url: string;
  title?: string | null;
  thumbnail?: string | null;
  quality?: string | null;
  audio_format?: string | null;
  estimated_size_mb?: number | null;
  status?: JobStatus;
  created_at: number;
};

export type HistoryItem = {
  id: string;
  source_url: string;
  title: string;
  thumbnail?: string | null;
  quality?: string | null;
  audio_format?: string | null;
  file_name?: string | null;
  file_size_mb?: number | null;
  completed_at: number;
};

export type UserPrefs = {
  quality: string;
  audioFormat: AudioFormat;
};

const DEFAULT_PREFS: UserPrefs = {
  quality: "best",
  audioFormat: "m4a",
};

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / private mode */
  }
}

export function loadStoredJobs(): StoredJob[] {
  const list = readJson<StoredJob[]>(JOBS_KEY, []);
  return Array.isArray(list) ? list : [];
}

export function saveStoredJobs(jobs: StoredJob[]): void {
  writeJson(JOBS_KEY, jobs.slice(0, 40));
}

export function upsertStoredJob(job: StoredJob): void {
  const list = loadStoredJobs().filter((j) => j.job_id !== job.job_id);
  list.unshift(job);
  saveStoredJobs(list);
}

export function removeStoredJob(jobId: string): void {
  saveStoredJobs(loadStoredJobs().filter((j) => j.job_id !== jobId));
}

export function loadHistory(): HistoryItem[] {
  const list = readJson<HistoryItem[]>(HISTORY_KEY, []);
  return Array.isArray(list) ? list : [];
}

export function pushHistory(item: Omit<HistoryItem, "id" | "completed_at">): void {
  const entry: HistoryItem = {
    ...item,
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    completed_at: Date.now(),
  };
  const list = loadHistory().filter(
    (h) => h.source_url !== entry.source_url || h.quality !== entry.quality,
  );
  list.unshift(entry);
  writeJson(HISTORY_KEY, list.slice(0, 30));
}

export function clearHistory(): void {
  writeJson(HISTORY_KEY, []);
}

export function loadPrefs(): UserPrefs {
  const prefs = readJson<Partial<UserPrefs>>(PREFS_KEY, {});
  return {
    quality: prefs.quality || DEFAULT_PREFS.quality,
    audioFormat:
      prefs.audioFormat === "mp3" || prefs.audioFormat === "m4a"
        ? prefs.audioFormat
        : DEFAULT_PREFS.audioFormat,
  };
}

export function savePrefs(prefs: UserPrefs): void {
  writeJson(PREFS_KEY, prefs);
}
