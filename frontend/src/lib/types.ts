export type JobStatus =
  | "queued"
  | "downloading"
  | "retrying"
  | "processing"
  | "done"
  | "failed"
  | "cancelled";

export type AudioFormat = "m4a" | "mp3";

export interface AvailableQuality {
  id: string;
  label: string;
  height: number | null;
  estimated_size_mb: number | null;
  fps: number | null;
  has_video: boolean;
  has_audio: boolean;
}

export interface PlaylistEntry {
  id: string;
  title: string | null;
  url: string;
  thumbnail: string | null;
  duration_seconds: number | null;
  uploader: string | null;
}

export interface PreviewResponse {
  title: string | null;
  thumbnail: string | null;
  duration_seconds: number | null;
  uploader: string | null;
  site: string | null;
  estimated_size_mb: number | null;
  url: string;
  available_qualities: AvailableQuality[];
  max_height: number | null;
  kind: "video" | "playlist";
  playlist_count: number | null;
  entries: PlaylistEntry[];
  max_playlist_select: number | null;
  entries_truncated: boolean;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  progress: number;
  error: string | null;
  error_hint: string | null;
  message: string | null;
  download_url: string | null;
  original_download_url?: string | null;
  expires_at: number | null;
  quality: string | null;
  audio_format: string | null;
  file_name?: string | null;
  file_size_mb?: number | null;
  original_file_size_mb?: number | null;
  has_trim?: boolean;
  has_previous_edit?: boolean;
  queue_position?: number | null;
}

export interface DownloadResponse {
  download_url: string;
  expires_in: number;
}

export interface TrimJobResponse {
  job_id: string;
  download_url: string;
  original_download_url?: string | null;
  expires_in: number;
  expires_at: number;
  file_size_mb: number;
  original_file_size_mb?: number | null;
  duration_seconds: number;
  kind?: "audio" | "video";
  has_trim?: boolean;
  has_previous_edit?: boolean;
  operation?: string | null;
}

export type EditJobResponse = TrimJobResponse;

export interface EditTimeRange {
  startSeconds: number;
  endSeconds: number;
}

export interface WaveformResponse {
  duration_seconds: number;
  peaks: number[];
  has_video: boolean;
  has_audio: boolean;
  kind: "audio" | "video";
  bar_count: number;
}

export interface CreateJobResponse {
  job_id: string;
}

export interface CreateBatchJobResponse {
  job_ids: string[];
}

export interface HealthResponse {
  status: string;
  redis: string;
  disk_usage_mb: number;
  disk_limit_mb: number;
  impersonate_available?: boolean;
  cookies_configured?: boolean;
  cookies_readable?: boolean;
  facebook_ready?: boolean;
  warnings?: string[];
}

export interface ApiErrorBody {
  detail?: string | { msg?: string }[] | Record<string, unknown>;
}

export const ACTIVE_STATUSES: JobStatus[] = [
  "queued",
  "downloading",
  "retrying",
  "processing",
];

export function isActiveStatus(status: JobStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}
