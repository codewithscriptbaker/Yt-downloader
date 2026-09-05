from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → repo root .env, then backend/.env
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    str(_REPO_ROOT / ".env"),
    str(Path(__file__).resolve().parents[1] / ".env"),
    ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    storage_path: str = "/data/storage"
    file_ttl_seconds: int = 3600
    max_jobs_per_ip: int = 2
    max_jobs_per_ip_per_hour: int = 10
    # Playlist preview/download caps (public anonymous downloader)
    max_playlist_entries: int = 30
    max_playlist_select: int = 5
    allowed_domains: str = (
        "youtube.com,www.youtube.com,m.youtube.com,youtu.be,music.youtube.com,"
        "tiktok.com,www.tiktok.com,vm.tiktok.com,"
        "instagram.com,www.instagram.com,"
        "facebook.com,www.facebook.com,m.facebook.com,mbasic.facebook.com,"
        "web.facebook.com,fb.watch,www.fb.watch,fb.com,www.fb.com"
    )
    max_file_size_mb: int = 500
    max_duration_seconds: int = 3600
    job_timeout_seconds: int = 600
    # Strategy / general attempt budget (also covers format fallback ladder)
    download_retry_attempts: int = 5
    download_retry_backoff_seconds: float = 3.0
    # Extra silent network/DNS retries per strategy (not shown to clients)
    download_network_retries: int = 5
    download_socket_timeout: int = 30
    tiktok_socket_timeout: int = 60
    facebook_socket_timeout: int = 60
    ytdlp_cookies_file: str = ""
    ytdlp_proxy: str = ""
    download_signing_secret: str = "change-me-in-production"
    download_url_ttl_seconds: int = 300
    # Auth (optional accounts — downloads work without login)
    auth_jwt_secret: str = "change-me-auth-secret"
    auth_token_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    captcha_secret: str = ""
    captcha_site_key: str = ""
    captcha_enabled: bool = False
    disk_usage_limit_mb: int = 20480
    celery_concurrency: int = 2
    log_level: str = "INFO"
    cors_origins: str = (
        "http://localhost:3005,http://localhost:3000,"
        "http://127.0.0.1:3005,http://127.0.0.1:3000"
    )

    @property
    def allowed_domain_set(self) -> set[str]:
        return {d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
