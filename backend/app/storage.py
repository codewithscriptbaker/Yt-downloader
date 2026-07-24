from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.config import Settings


class StorageBackend(ABC):
    """Abstraction so local disk can later move to S3-compatible storage."""

    @abstractmethod
    def ensure_dirs(self) -> None: ...

    @abstractmethod
    def tmp_dir(self, job_id: str) -> Path: ...

    @abstractmethod
    def ready_path(self, opaque_token: str, filename: str) -> Path: ...

    @abstractmethod
    def disk_usage_bytes(self) -> int: ...

    @abstractmethod
    def delete_path(self, path: str) -> None: ...


class LocalDiskStorage(StorageBackend):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.tmp = self.root / "tmp"
        self.ready = self.root / "ready"

    def ensure_dirs(self) -> None:
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.ready.mkdir(parents=True, exist_ok=True)

    def tmp_dir(self, job_id: str) -> Path:
        path = self.tmp / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ready_path(self, opaque_token: str, filename: str) -> Path:
        dest_dir = self.ready / opaque_token
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir / filename

    def disk_usage_bytes(self) -> int:
        total = 0
        if not self.root.exists():
            return 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def delete_path(self, path: str) -> None:
        p = Path(path)
        if p.is_file():
            p.unlink(missing_ok=True)
            parent = p.parent
            if parent != self.ready and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


_storage: Optional[LocalDiskStorage] = None


def get_storage(settings: Optional[Settings] = None) -> LocalDiskStorage:
    global _storage
    if _storage is None:
        from app.config import get_settings

        cfg = settings or get_settings()
        _storage = LocalDiskStorage(cfg.storage_path)
        _storage.ensure_dirs()
    return _storage


def disk_usage_mb(settings: Settings) -> float:
    return get_storage(settings).disk_usage_bytes() / (1024 * 1024)


def is_disk_full(settings: Settings) -> bool:
    return disk_usage_mb(settings) >= settings.disk_usage_limit_mb
