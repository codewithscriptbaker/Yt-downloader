from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from app.models import ACTIVE_STATUSES, JobRecord, JobStatus
from app.redis_client import get_redis

JOB_KEY = "job:{job_id}"
IP_ACTIVE_KEY = "ip:active:{ip}"
IP_HOURLY_KEY = "ip:hourly:{ip}"
JOB_STATUS_CHANNEL = "job:status:{job_id}"
JOB_INDEX = "jobs:index"


def _job_key(job_id: str) -> str:
    return JOB_KEY.format(job_id=job_id)


def _ip_active_key(ip: str) -> str:
    return IP_ACTIVE_KEY.format(ip=ip)


def _ip_hourly_key(ip: str) -> str:
    return IP_HOURLY_KEY.format(ip=ip)


def create_job(
    url: str,
    url_domain: str,
    ip: str,
    ttl_seconds: int,
    *,
    quality: str = "best",
    audio_format: str = "m4a",
) -> JobRecord:
    r = get_redis()
    job_id = str(uuid.uuid4())
    record = JobRecord(
        job_id=job_id,
        url=url,
        url_domain=url_domain,
        status=JobStatus.QUEUED,
        progress=0,
        created_at=time.time(),
        ip=ip,
        quality=quality,
        audio_format=audio_format,
        message="Waiting for a free worker…",
    )
    pipe = r.pipeline()
    pipe.set(_job_key(job_id), record.model_dump_json(), ex=ttl_seconds + 3600)
    pipe.sadd(_ip_active_key(ip), job_id)
    pipe.expire(_ip_active_key(ip), ttl_seconds + 3600)
    pipe.sadd(JOB_INDEX, job_id)
    pipe.execute()
    publish_job_update(record)
    return record


def get_job(job_id: str) -> Optional[JobRecord]:
    raw = get_redis().get(_job_key(job_id))
    if not raw:
        return None
    return JobRecord.model_validate_json(raw)


def save_job(record: JobRecord, ttl_seconds: int) -> None:
    r = get_redis()
    expire = ttl_seconds + 3600
    if record.expires_at:
        remaining = max(int(record.expires_at - time.time()) + 60, 60)
        expire = remaining
    r.set(_job_key(record.job_id), record.model_dump_json(), ex=expire)
    if record.status in ACTIVE_STATUSES:
        r.sadd(_ip_active_key(record.ip), record.job_id)
    else:
        r.srem(_ip_active_key(record.ip), record.job_id)
    publish_job_update(record)


def publish_job_update(record: JobRecord) -> None:
    from app.errors import hint_for_error

    # Hide internal retry bookkeeping from clients — always look like a normal download.
    status = record.status
    message = record.message
    if status == JobStatus.RETRYING:
        status = JobStatus.DOWNLOADING
        message = "Downloading…"

    payload = {
        "job_id": record.job_id,
        "status": status.value,
        "progress": record.progress,
        "error": record.error,
        "error_hint": hint_for_error(record.error) if record.error else None,
        "message": message,
        "expires_at": record.expires_at,
        "quality": record.quality,
        "audio_format": record.audio_format,
        "file_name": record.file_name,
        "file_size_mb": getattr(record, "file_size_mb", None),
    }
    get_redis().publish(JOB_STATUS_CHANNEL.format(job_id=record.job_id), json.dumps(payload))


def update_job_fields(job_id: str, ttl_seconds: int, **fields) -> Optional[JobRecord]:
    record = get_job(job_id)
    if not record:
        return None
    data = record.model_dump()
    data.update(fields)
    updated = JobRecord.model_validate(data)
    save_job(updated, ttl_seconds)
    return updated


def count_active_jobs_for_ip(ip: str) -> int:
    r = get_redis()
    job_ids = r.smembers(_ip_active_key(ip))
    active = 0
    for job_id in job_ids:
        job = get_job(job_id)
        if job and job.status in ACTIVE_STATUSES:
            active += 1
        else:
            r.srem(_ip_active_key(ip), job_id)
    return active


def increment_hourly_submissions(ip: str, limit: int) -> int:
    """Increment and return current count within the rolling hour window."""
    r = get_redis()
    key = _ip_hourly_key(ip)
    count = r.incr(key)
    if count == 1:
        r.expire(key, 3600)
    return int(count)


def get_hourly_submissions(ip: str) -> int:
    raw = get_redis().get(_ip_hourly_key(ip))
    return int(raw) if raw else 0


def list_all_job_ids() -> set[str]:
    return set(get_redis().smembers(JOB_INDEX))


def delete_job_record(job_id: str) -> None:
    r = get_redis()
    job = get_job(job_id)
    if job:
        r.srem(_ip_active_key(job.ip), job_id)
    r.delete(_job_key(job_id))
    r.srem(JOB_INDEX, job_id)
