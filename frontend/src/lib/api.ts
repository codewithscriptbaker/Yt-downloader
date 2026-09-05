import { getApiBase, resolveApiUrl } from "./config";
import { loadToken } from "./authStorage";
import type {
  ApiErrorBody,
  AudioFormat,
  CreateBatchJobResponse,
  CreateJobResponse,
  DownloadResponse,
  HealthResponse,
  JobStatusResponse,
  PreviewResponse,
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailMessage(body: ApiErrorBody | null, fallback: string): string {
  if (!body?.detail) return fallback;
  const d = body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((item) => (typeof item === "object" && item?.msg ? item.msg : String(item)))
      .join("; ");
  }
  return fallback;
}

async function request<T>(
  path: string,
  init?: RequestInit & { auth?: boolean },
): Promise<T> {
  const url = `${getApiBase()}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (init?.auth !== false) {
    const token = loadToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers,
    });
  } catch {
    throw new ApiError(
      "Cannot reach the API. Is the backend running?",
      0,
    );
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let body: ApiErrorBody | null = null;
  if (text) {
    try {
      body = JSON.parse(text) as ApiErrorBody;
    } catch {
      body = null;
    }
  }

  if (!res.ok) {
    throw new ApiError(
      detailMessage(body, res.statusText || "Request failed"),
      res.status,
    );
  }

  return (body ?? {}) as T;
}

/** Transient HTTP / network statuses worth silent retry. */
export function isTransientApiError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  return err.status === 0 || err.status === 408 || err.status === 429
    || err.status === 502 || err.status === 503 || err.status === 504;
}

async function sleep(ms: number): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

/**
 * Preview with silent retries on transient failures.
 * Only throws after the budget is exhausted (real failure).
 */
export async function fetchPreviewWithRetry(
  url: string,
  captchaToken?: string | null,
  opts?: { attempts?: number; onRetry?: (attempt: number, max: number) => void },
): Promise<PreviewResponse> {
  const max = opts?.attempts ?? 3;
  let lastError: unknown;
  for (let attempt = 1; attempt <= max; attempt++) {
    try {
      return await fetchPreview(url, captchaToken);
    } catch (err) {
      lastError = err;
      const retryable = isTransientApiError(err);
      if (!retryable || attempt >= max) break;
      opts?.onRetry?.(attempt, max);
      await sleep(600 * attempt);
    }
  }
  throw lastError;
}

export async function fetchPreview(
  url: string,
  captchaToken?: string | null,
): Promise<PreviewResponse> {
  return request<PreviewResponse>("/api/preview", {
    method: "POST",
    body: JSON.stringify({
      url,
      captcha_token: captchaToken || null,
    }),
  });
}

export async function createJob(opts: {
  url: string;
  quality: string;
  audioFormat: AudioFormat;
  captchaToken?: string | null;
}): Promise<CreateJobResponse> {
  return request<CreateJobResponse>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({
      url: opts.url,
      quality: opts.quality,
      audio_format: opts.audioFormat,
      captcha_token: opts.captchaToken || null,
    }),
  });
}

export async function createBatchJobs(opts: {
  urls: string[];
  quality: string;
  audioFormat: AudioFormat;
  captchaToken?: string | null;
}): Promise<CreateBatchJobResponse> {
  return request<CreateBatchJobResponse>("/api/jobs/batch", {
    method: "POST",
    body: JSON.stringify({
      urls: opts.urls,
      quality: opts.quality,
      audio_format: opts.audioFormat,
      captcha_token: opts.captchaToken || null,
    }),
  });
}

export async function getJob(jobId: string): Promise<JobStatusResponse> {
  return request<JobStatusResponse>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export async function getDownloadLink(jobId: string): Promise<DownloadResponse> {
  return request<DownloadResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/download`,
  );
}

export async function cancelJob(jobId: string): Promise<void> {
  await request<void>(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { auth: false });
}

export type AuthUserDto = {
  user_id: string;
  email: string;
  name: string;
};

export type AuthResponseDto = {
  access_token: string;
  token_type: string;
  user: AuthUserDto;
};

export type RemoteHistoryItem = {
  id: string;
  source_url: string;
  title: string;
  thumbnail: string | null;
  quality: string | null;
  audio_format: string | null;
  file_name: string | null;
  file_size_mb: number | null;
  completed_at: number;
};

export async function signup(opts: {
  email: string;
  password: string;
  name?: string;
}): Promise<AuthResponseDto> {
  return request<AuthResponseDto>("/api/auth/signup", {
    method: "POST",
    auth: false,
    body: JSON.stringify({
      email: opts.email,
      password: opts.password,
      name: opts.name || "",
    }),
  });
}

export async function login(opts: {
  email: string;
  password: string;
}): Promise<AuthResponseDto> {
  return request<AuthResponseDto>("/api/auth/login", {
    method: "POST",
    auth: false,
    body: JSON.stringify(opts),
  });
}

export async function fetchMe(): Promise<AuthUserDto> {
  return request<AuthUserDto>("/api/auth/me");
}

export async function fetchRemoteHistory(): Promise<RemoteHistoryItem[]> {
  const res = await request<{ items: RemoteHistoryItem[] }>("/api/history");
  return res.items || [];
}

export async function postRemoteHistory(item: {
  source_url: string;
  title: string;
  thumbnail?: string | null;
  quality?: string | null;
  audio_format?: string | null;
  file_name?: string | null;
  file_size_mb?: number | null;
}): Promise<RemoteHistoryItem> {
  return request<RemoteHistoryItem>("/api/history", {
    method: "POST",
    body: JSON.stringify(item),
  });
}

export async function clearRemoteHistory(): Promise<void> {
  await request<void>("/api/history", { method: "DELETE" });
}

/** Absolute URL for browser download (handles cross-origin API base). */
export function absoluteDownloadUrl(pathOrUrl: string): string {
  return resolveApiUrl(pathOrUrl);
}
