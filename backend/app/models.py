from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


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

QualityLiteral = Literal["best", "1080", "720", "360", "audio"]
AudioFormatLiteral = Literal["m4a", "mp3"]


class CreateJobRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    captcha_token: Optional[str] = None
    quality: QualityLiteral = "best"
    audio_format: AudioFormatLiteral = "m4a"


class CreateJobResponse(BaseModel):
    job_id: str


class PreviewRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    captcha_token: Optional[str] = None


class PreviewResponse(BaseModel):
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    duration_seconds: Optional[int] = None
    uploader: Optional[str] = None
    site: Optional[str] = None
    estimated_size_mb: Optional[float] = None
    url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = 0
    error: Optional[str] = None
    error_hint: Optional[str] = None
    message: Optional[str] = None
    download_url: Optional[str] = None
    expires_at: Optional[float] = None
    quality: Optional[str] = None
    audio_format: Optional[str] = None


class DownloadResponse(BaseModel):
    download_url: str
    expires_in: int


class HealthResponse(BaseModel):
    status: str
    redis: str
    disk_usage_mb: float
    disk_limit_mb: int


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
    opaque_token: Optional[str] = None
    ip: str
    celery_task_id: Optional[str] = None
    quality: str = "best"
    audio_format: str = "m4a"
