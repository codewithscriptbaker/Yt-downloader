"""
Start all local MediaPort processes with a 4s pause between each.

Usage (from repo root):
  py scripts/run_all.py

Loads repo-root .env, then starts Redis → API → Celery worker → beat → frontend.
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
ROOT_ENV = ROOT / ".env"

STORAGE = ROOT / "data" / "storage"

children: list[subprocess.Popen] = []


def log(msg: str) -> None:
    print(f"[run_all] {msg}", flush=True)


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env loader (KEY=VALUE, ignores comments/blank lines)."""
    loaded: dict[str, str] = {}
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        loaded[key] = value
    return loaded


def apply_root_env() -> dict[str, str]:
    """Load root .env into os.environ (.env wins so LAN/phone settings apply after edit)."""
    loaded = load_dotenv(ROOT_ENV)
    for key, value in loaded.items():
        os.environ[key] = value
    return loaded


def python_bin() -> str:
    if VENV_PY.is_file():
        return str(VENV_PY)
    return sys.executable


def celery_bin() -> list[str]:
    if VENV_CELERY.is_file():
        return [str(VENV_CELERY)]
    return [python_bin(), "-m", "celery"]


def api_port() -> int:
    return int(os.environ.get("DUKTOO_API_PORT", "8009"))


def frontend_port() -> int:
    return int(os.environ.get("DUKTOO_FRONTEND_PORT", "3005"))


def backend_env() -> dict[str, str]:
    port_fe = frontend_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")
    env.setdefault("STORAGE_PATH", str(STORAGE))
    env.setdefault(
        "CORS_ORIGINS",
        f"http://localhost:{port_fe},http://127.0.0.1:{port_fe},http://localhost:3000",
    )
    env.setdefault("CAPTCHA_ENABLED", "false")
    env.setdefault("DOWNLOAD_SIGNING_SECRET", "dev-secret")
    return env


def frontend_env() -> dict[str, str]:
    port_api = api_port()
    env = os.environ.copy()
    # Always point Next at the API port from .env (override empty/stale values)
    env["NEXT_PUBLIC_API_BASE"] = os.environ.get(
        "NEXT_PUBLIC_API_BASE", f"http://localhost:{port_api}"
    ) or f"http://localhost:{port_api}"
    env["NEXT_PUBLIC_WS_BASE"] = os.environ.get(
        "NEXT_PUBLIC_WS_BASE", f"ws://localhost:{port_api}"
    ) or f"ws://localhost:{port_api}"
    env.setdefault(
        "NEXT_PUBLIC_CAPTCHA_ENABLED",
        os.environ.get("NEXT_PUBLIC_CAPTCHA_ENABLED", "false"),
    )
    if "NEXT_PUBLIC_CAPTCHA_SITE_KEY" in os.environ:
        env["NEXT_PUBLIC_CAPTCHA_SITE_KEY"] = os.environ["NEXT_PUBLIC_CAPTCHA_SITE_KEY"]
    return env


def sync_frontend_env_local(env: dict[str, str]) -> None:
    """Write frontend/.env.local so Next.dev picks up NEXT_PUBLIC_* (not only process env)."""
    path = FRONTEND / ".env.local"
    captcha_enabled = env.get("NEXT_PUBLIC_CAPTCHA_ENABLED", "false")
    captcha_key = env.get("NEXT_PUBLIC_CAPTCHA_SITE_KEY", "")
    path.write_text(
        "# Auto-synced from root .env by scripts/run_all.py — do not edit by hand.\n"
        f"NEXT_PUBLIC_API_BASE={env['NEXT_PUBLIC_API_BASE']}\n"
        f"NEXT_PUBLIC_WS_BASE={env['NEXT_PUBLIC_WS_BASE']}\n"
        f"NEXT_PUBLIC_CAPTCHA_ENABLED={captcha_enabled}\n"
        f"NEXT_PUBLIC_CAPTCHA_SITE_KEY={captcha_key}\n",
        encoding="utf-8",
    )
    log(f"Synced {path.name} → API {env['NEXT_PUBLIC_API_BASE']}")


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
    container = os.environ.get("DUKTOO_REDIS_CONTAINER", "yt-redis")
    log(f"Ensuring Redis container '{container}'…")
    inspect = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    if inspect.returncode == 0:
        running = inspect.stdout.strip().lower() == "true"
        if running:
            log("Redis already running")
            return
        log("Starting existing Redis container")
        subprocess.run(["docker", "start", container], check=False)
        return

    log("Creating Redis container")
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
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

    loaded = apply_root_env()
    if loaded:
        log(f"Loaded {len(loaded)} vars from {ROOT_ENV}")
    else:
        log(f"No {ROOT_ENV.name} found — using defaults")

    port_api = api_port()
    port_fe = frontend_port()
    sleep_seconds = float(os.environ.get("DUKTOO_START_SLEEP", "4"))

    STORAGE.mkdir(parents=True, exist_ok=True)

    log(f"Root: {ROOT}")
    log(f"API : http://localhost:{port_api}")
    log(f"Web : http://localhost:{port_fe}")
    log(f"NEXT_PUBLIC_API_BASE={frontend_env().get('NEXT_PUBLIC_API_BASE')}")
    log(f"Sleep between starts: {sleep_seconds}s")
    print(flush=True)

    try:
        ensure_redis()
        time.sleep(sleep_seconds)

        py = python_bin()
        env_b = backend_env()
        env_f = frontend_env()
        sync_frontend_env_local(env_f)

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
                str(port_api),
            ],
            cwd=BACKEND,
            env=env_b,
        )
        time.sleep(sleep_seconds)

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
        time.sleep(sleep_seconds)

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
        time.sleep(sleep_seconds)

        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        # Bind 0.0.0.0 so phones on the same Wi‑Fi can open the UI
        start(
            "Frontend",
            [npm, "run", "dev", "--", "-H", "0.0.0.0", "-p", str(port_fe)],
            cwd=FRONTEND,
            env=env_f,
        )

        api_base = env_f.get("NEXT_PUBLIC_API_BASE", f"http://localhost:{port_api}")
        print(flush=True)
        log("All processes launched.")
        log(f"PC  : http://localhost:{port_fe}")
        log(f"Phone (same Wi‑Fi): use your LAN IP, e.g. http://<LAN-IP>:{port_fe}")
        log(f"API base used by frontend: {api_base}")
        log(f"API docs http://localhost:{port_api}/docs")
        log("Press Ctrl+C to stop everything.")
        print(flush=True)

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
