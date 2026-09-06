from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.formats import normalize_quality, quality_is_allowed


class JobStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    RETRYING = "retrying"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.RETRYING,
    JobStatus.PROCESSING,
}

AudioFormatLiteral = Literal["m4a", "mp3"]


class CreateJobRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    captcha_token: Optional[str] = None
    # "best" | "audio" | height like "1080" / "720" (from preview available_qualities)
    quality: str = "best"
    audio_format: AudioFormatLiteral = "m4a"

    @field_validator("quality")
    @classmethod
    def _quality_ok(cls, value: str) -> str:
        q = normalize_quality(value)
        if not quality_is_allowed(q):
            raise ValueError("quality must be best, audio, or a height like 720 / 1080p60")
        return q


class CreateJobResponse(BaseModel):
    job_id: str


class CreateBatchJobRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1)
    captcha_token: Optional[str] = None
    quality: str = "best"
    audio_format: AudioFormatLiteral = "m4a"

    @field_validator("quality")
    @classmethod
    def _quality_ok(cls, value: str) -> str:
        q = normalize_quality(value)
        if not quality_is_allowed(q):
            raise ValueError("quality must be best, audio, or a height like 720 / 1080p60")
        return q

    @field_validator("urls")
    @classmethod
    def _urls_ok(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            u = (raw or "").strip()
            if len(u) < 8 or len(u) > 2048:
                raise ValueError("each url must be 8–2048 characters")
            if u in seen:
                continue
            seen.add(u)
            cleaned.append(u)
        if not cleaned:
            raise ValueError("urls must not be empty")
        return cleaned


class CreateBatchJobResponse(BaseModel):
    job_ids: list[str]


class PreviewRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    captcha_token: Optional[str] = None


class AvailableQuality(BaseModel):
    id: str
    label: str
    height: Optional[int] = None
    estimated_size_mb: Optional[float] = None
    fps: Optional[int] = None
    has_video: bool = True
    has_audio: bool = True


class PlaylistEntry(BaseModel):
    id: str
    title: Optional[str] = None
    url: str
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    uploader: Optional[str] = None


class PreviewResponse(BaseModel):
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    uploader: Optional[str] = None
    site: Optional[str] = None
    estimated_size_mb: Optional[float] = None
    url: str
    available_qualities: list[AvailableQuality] = Field(default_factory=list)
    max_height: Optional[int] = None
    kind: Literal["video", "playlist"] = "video"
    playlist_count: Optional[int] = None
    entries: list[PlaylistEntry] = Field(default_factory=list)
    max_playlist_select: Optional[int] = None
    entries_truncated: bool = False


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    error: Optional[str] = None
    error_hint: Optional[str] = None
    message: Optional[str] = None
    download_url: Optional[str] = None
    original_download_url: Optional[str] = None
    expires_at: Optional[float] = None
    quality: Optional[str] = None
    audio_format: Optional[str] = None
    file_name: Optional[str] = None
    file_size_mb: Optional[float] = None
    original_file_size_mb: Optional[float] = None
    has_trim: bool = False
    has_previous_edit: bool = False
    queue_position: Optional[int] = None


class DownloadResponse(BaseModel):
    download_url: str
    expires_in: int


class TrimJobRequest(BaseModel):
    """Trim a ready media file to [start_seconds, end_seconds)."""

    start_seconds: float = Field(..., ge=0, le=86_400)
    end_seconds: float = Field(..., gt=0, le=86_400)
    # Video only: force re-encode for frame-accurate cuts (slower).
    precise: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> "TrimJobRequest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if self.end_seconds - self.start_seconds < 0.25:
            raise ValueError("Select at least 0.25 seconds to keep")
        return self


class EditTimeRange(BaseModel):
    start_seconds: float = Field(..., ge=0, le=86_400)
    end_seconds: float = Field(..., gt=0, le=86_400)

    @model_validator(mode="after")
    def _end_after_start(self) -> "EditTimeRange":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if self.end_seconds - self.start_seconds < 0.25:
            raise ValueError("Each segment must be at least 0.25 seconds")
        return self


class ComposeEditRequest(BaseModel):
    """Keep multiple ranges (in order) and stitch into one file."""

    ranges: list[EditTimeRange] = Field(..., min_length=1, max_length=20)
    precise: bool = False


class InsertEditFormMeta(BaseModel):
    """Validated form fields for multipart insert (file uploaded separately)."""

    at_seconds: float = Field(..., ge=0, le=86_400)
    replace_audio: bool = False
    crossfade_seconds: float = Field(0.0, ge=0, le=2.0)


class TrimJobResponse(BaseModel):
    job_id: str
    download_url: str
    original_download_url: Optional[str] = None
    expires_in: int
    expires_at: float
    file_size_mb: float
    original_file_size_mb: Optional[float] = None
    duration_seconds: float
    kind: Literal["audio", "video"] = "audio"
    has_trim: bool = True
    has_previous_edit: bool = False
    operation: Optional[str] = None


# Alias for compose / insert / restore / undo
EditJobResponse = TrimJobResponse


class WaveformResponse(BaseModel):
    duration_seconds: float
    peaks: list[float]
    has_video: bool
    has_audio: bool
    kind: Literal["audio", "video"]
    bar_count: int


class HealthResponse(BaseModel):
    status: str
    redis: str
    disk_usage_mb: float
    disk_limit_mb: int
    impersonate_available: bool = False
    cookies_configured: bool = False
    cookies_readable: bool = False
    facebook_ready: bool = False
    warnings: list[str] = []


class JobRecord(BaseModel):
    job_id: str
    url: str
    url_domain: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    error: Optional[str] = None
    message: Optional[str] = None
    created_at: float
    expires_at: Optional[float] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_size_mb: Optional[float] = None
    original_file_path: Optional[str] = None
    original_file_name: Optional[str] = None
    original_file_size_mb: Optional[float] = None
    has_trim: bool = False
    previous_file_path: Optional[str] = None
    previous_file_name: Optional[str] = None
    previous_file_size_mb: Optional[float] = None
    has_previous_edit: bool = False
    opaque_token: Optional[str] = None
    ip: str
    celery_task_id: Optional[str] = None
    quality: str = "best"
    audio_format: str = "m4a"
