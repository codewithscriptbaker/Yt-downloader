"""
Start all local Duktoo Media processes with a 4s pause between each.

Usage (from repo root):
  py scripts/run_all.py

Stops everything with Ctrl+C.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PY = BACKEND / ".venv" / "Scripts" / "python.exe"
VENV_CELERY = BACKEND / ".venv" / "Scripts" / "celery.exe"

# Match current local .env setup
API_PORT = int(os.environ.get("DUKTOO_API_PORT", "8009"))
FRONTEND_PORT = int(os.environ.get("DUKTOO_FRONTEND_PORT", "3005"))
REDIS_CONTAINER = os.environ.get("DUKTOO_REDIS_CONTAINER", "yt-redis")
SLEEP_SECONDS = float(os.environ.get("DUKTOO_START_SLEEP", "4"))

STORAGE = ROOT / "data" / "storage"

children: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[run_all] {msg}", flush=True)


def python_bin() -> str:
    if VENV_PY.is_file():
        return str(VENV_PY)
    return sys.executable


def celery_bin() -> list[str]:
    if VENV_CELERY.is_file():
        return [str(VENV_CELERY)]
    return [python_bin(), "-m", "celery"]


def backend_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")
    env.setdefault("STORAGE_PATH", str(STORAGE))
    env.setdefault("CORS_ORIGINS", f"http://localhost:{FRONTEND_PORT},http://localhost:3000")
    env.setdefault("CAPTCHA_ENABLED", "false")
    return env


def frontend_env() -> dict[str, str]:
    env = os.environ.copy()
    env["NEXT_PUBLIC_API_BASE"] = f"http://localhost:{API_PORT}"
    env["NEXT_PUBLIC_WS_BASE"] = f"ws://localhost:{API_PORT}"
    env["NEXT_PUBLIC_CAPTCHA_ENABLED"] = "false"
    return env


def start(name: str, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    log(f"Starting {name}: {' '.join(args)}")
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=env or os.environ.copy(),
    )
    children.append(proc)
    log(f"{name} pid={proc.pid}")
    return proc


def ensure_redis() -> None:
    """Start Redis docker container if needed."""
    log(f"Ensuring Redis container '{REDIS_CONTAINER}'…")
    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", REDIS_CONTAINER],
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        running = inspect.stdout.strip().lower() == "true"
        if running:
            log("Redis already running")
            return
        log("Starting existing Redis container")
        subprocess.run(["docker", "start", REDIS_CONTAINER], check=False)
        return

    log("Creating Redis container")
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REDIS_CONTAINER,
            "-p",
            "6379:6379",
            "redis:7-alpine",
        ],
        check=False,
    )


def stop_all() -> None:
    log("Stopping child processes…")
    for proc in reversed(children):
        if proc.poll() is not None:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as exc:
            log(f"Could not stop pid={proc.pid}: {exc}")
            try:
                proc.kill()
            except Exception:
                pass
    log("Done")


def main() -> int:
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        log(f"Expected backend/ and frontend/ under {ROOT}")
        return 1

    STORAGE.mkdir(parents=True, exist_ok=True)

    log(f"Root: {ROOT}")
    log(f"API : http://localhost:{API_PORT}")
    log(f"Web : http://localhost:{FRONTEND_PORT}")
    log(f"Sleep between starts: {SLEEP_SECONDS}s")
    print(flush=True)

    try:
        ensure_redis()
        time.sleep(SLEEP_SECONDS)

        py = python_bin()
        env_b = backend_env()

        start(
            "API",
            [
                py,
                "-m",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                "0.0.0.0",
                "--port",
                str(API_PORT),
            ],
            cwd=BACKEND,
            env=env_b,
        )
        time.sleep(SLEEP_SECONDS)

        start(
            "Celery worker",
            [
                *celery_bin(),
                "-A",
                "app.celery_app.celery_app",
                "worker",
                "--loglevel=INFO",
                "--concurrency=2",
                "--pool=solo",
            ],
            cwd=BACKEND,
            env=env_b,
        )
        time.sleep(SLEEP_SECONDS)

        start(
            "Celery beat",
            [
                *celery_bin(),
                "-A",
                "app.celery_app.celery_app",
                "beat",
                "--loglevel=INFO",
            ],
            cwd=BACKEND,
            env=env_b,
        )
        time.sleep(SLEEP_SECONDS)

        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        start(
            "Frontend",
            [npm, "run", "dev", "--", "-p", str(FRONTEND_PORT)],
            cwd=FRONTEND,
            env=frontend_env(),
        )

        print(flush=True)
        log("All processes launched.")
        log(f"Open http://localhost:{FRONTEND_PORT}")
        log(f"API docs http://localhost:{API_PORT}/docs")
        log("Press Ctrl+C to stop everything.")
        print(flush=True)

        # Wait until a critical process exits or user interrupts
        while True:
            for proc in children:
                code = proc.poll()
                if code is not None:
                    log(f"Process pid={proc.pid} exited with code {code}")
                    return code or 1
            time.sleep(1)

    except KeyboardInterrupt:
        print(flush=True)
        log("Interrupted")
        return 0
    finally:
        stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
