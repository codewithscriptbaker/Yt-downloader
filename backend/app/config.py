from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
        "instagram.com,www.instagram.com"
    )
    max_file_size_mb: int = 500
    max_duration_seconds: int = 3600
    job_timeout_seconds: int = 600
    download_retry_attempts: int = 3
    download_retry_backoff_seconds: float = 2.0
    download_socket_timeout: int = 30
    tiktok_socket_timeout: int = 60
    ytdlp_cookies_file: str = ""
    ytdlp_proxy: str = ""
    download_signing_secret: str = "change-me-in-production"
    download_url_ttl_seconds: int = 300
    captcha_secret: str = ""
    captcha_site_key: str = ""
    captcha_enabled: bool = False
    disk_usage_limit_mb: int = 20480
    celery_concurrency: int = 2
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost"

    @property
    def allowed_domain_set(self) -> set[str]:
        return {d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
