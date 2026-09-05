# MediaPort

Public anonymous media downloader for YouTube, TikTok, Instagram, and Facebook.

## Stack

- Next.js frontend
- FastAPI + Celery + Redis + yt-dlp
- Nginx reverse proxy
- Docker Compose on a single VM

---

## Preferred: run via terminal (local development)

Use this when you want hot reload and fast iteration. You only need Redis (and ffmpeg) as external dependencies.

### Check if Redis is installed / running

```powershell
# Redis as Docker container (recommended on Windows)
docker ps -a --filter "name=redis"

# Ping Redis inside container
docker exec yt-redis redis-cli ping
# Expected: PONG

# Or check port 6379
Test-NetConnection localhost -Port 6379
# TcpTestSucceeded : True  => Redis is listening

# Native Redis (if installed on Windows and on PATH)
redis-cli ping
```



### One-time setup

```powershell
cd C:\Users\User\Desktop\Duktoo\YT

# Stop full Docker stack if it is running
docker compose down

# Start Redis only
docker run -d --name yt-redis -p 6379:6379 redis:7-alpine

# Backend Python venv + deps
cd C:\Users\User\Desktop\Duktoo\YT\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `backend\.env` (or set the same vars in each terminal):

```env
REDIS_URL=redis://localhost:6379/0
STORAGE_PATH=C:\Users\User\Desktop\Duktoo\YT\data\storage
DOWNLOAD_SIGNING_SECRET=dev-secret
CAPTCHA_ENABLED=false
CORS_ORIGINS=http://localhost:3000
```

Install **ffmpeg** and make sure it is on your PATH (required by yt-dlp for merges).

```powershell
# Frontend deps
cd C:\Users\User\Desktop\Duktoo\YT\frontend
npm install
```



### Run everything (one command)

```powershell
cd C:\Users\User\Desktop\Duktoo\YT
py scripts\run_all.py
```

Starts Redis → API (`8009`) → Celery worker → Celery beat → frontend (`3005`), waiting **4 seconds** between each. Press **Ctrl+C** to stop all.

Optional overrides:

```powershell
$env:DUKTOO_API_PORT="8009"
$env:DUKTOO_FRONTEND_PORT="3005"
$env:DUKTOO_START_SLEEP="4"
py scripts\run_all.py
```

### Run (4 terminals)

**Terminal 1 — API (hot reload)**

```powershell
cd C:\Users\User\Desktop\Duktoo\YT\backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
$env:REDIS_URL="redis://localhost:6379/0"
$env:STORAGE_PATH="C:\Users\User\Desktop\Duktoo\YT\data\storage"
$env:CORS_ORIGINS="http://localhost:3000"
py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8009
```

**Terminal 2 — Celery worker**

```powershell
cd C:\Users\User\Desktop\Duktoo\YT\backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
$env:REDIS_URL="redis://localhost:6379/0"
$env:STORAGE_PATH="C:\Users\User\Desktop\Duktoo\YT\data\storage"
celery -A app.celery_app.celery_app worker --loglevel=INFO --concurrency=2 --pool=solo
```

(`--pool=solo` is more reliable on Windows.)

**Terminal 3 — Celery beat (TTL cleanup)**

```powershell
cd C:\Users\User\Desktop\Duktoo\YT\backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
$env:REDIS_URL="redis://localhost:6379/0"
$env:STORAGE_PATH="C:\Users\User\Desktop\Duktoo\YT\data\storage"
celery -A app.celery_app.celery_app beat --loglevel=INFO
```

**Terminal 4 — Next.js (hot reload)**

```powershell
cd C:\Users\User\Desktop\Duktoo\YT\frontend
$env:NEXT_PUBLIC_API_BASE="http://localhost:8000"
$env:NEXT_PUBLIC_WS_BASE="ws://localhost:8000"
$env:NEXT_PUBLIC_CAPTCHA_ENABLED="false"
npm run dev -- -p 3005
```

Open: **[http://localhost:3000](http://localhost:3000)**

API docs: **[http://localhost:8000/docs](http://localhost:8000/docs)**

### Local tips

- API reloads with `--reload`; frontend reloads with `npm run dev`.
- Restart worker/beat after changing Celery task code.
- Without Nginx, always set `NEXT_PUBLIC_API_BASE` and `NEXT_PUBLIC_WS_BASE` as above.
- If Redis container already exists but is stopped: `docker start yt-redis`



### Redis helper commands

```powershell
# List Redis containers
docker ps -a --filter "name=redis"

# Start / stop Redis container
docker start yt-redis
docker stop yt-redis

# Ping
docker exec yt-redis redis-cli ping
# Expected: PONG

# Port check
Test-NetConnection localhost -Port 6379
```



### Local unit checks (no Redis needed)

```powershell
cd C:\Users\User\Desktop\Duktoo\YT\backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
py tests\test_local.py
```

---



## Alternative: Docker Compose (full stack)

```powershell
cd C:\Users\User\Desktop\Duktoo\YT
copy .env.example .env
# Edit DOWNLOAD_SIGNING_SECRET (and CAPTCHA keys for public launch)
docker compose up --build -d
```

Open: **[http://localhost](http://localhost)**

```powershell
# Stop
docker compose down

# Logs
docker compose logs -f

# Rebuild one service
docker compose build backend
docker compose up -d backend worker beat
```

---



## API


| Method | Path                      | Purpose                                                                     |
| ------ | ------------------------- | --------------------------------------------------------------------------- |
| GET    | `/api/health`             | Health + disk usage                                                         |
| POST   | `/api/jobs`               | `{ "url", "quality?", "audio_format?", "captcha_token?" }` → `{ "job_id" }` |
| POST   | `/api/jobs/batch`         | `{ "urls": [...], "quality?", … }` → `{ "job_ids": [...] }` (max 5)         |
| POST   | `/api/preview`            | Metadata + qualities; playlists return `kind: "playlist"` + `entries`       |
| GET    | `/api/jobs/{id}`          | Status (polling fallback); includes `error_hint`, `expires_at`, `quality`   |
| GET    | `/api/jobs/{id}/download` | Signed download URL                                                         |
| GET    | `/api/jobs/{id}/file`     | Stream file with token                                                      |
| DELETE | `/api/jobs/{id}`          | Cancel own job                                                              |
| WS     | `/ws/jobs/{id}`           | Live status                                                                 |


`quality`: `best` · `audio` · or a height from preview (`1080`, `720`, …)  
`audio_format` (when quality is `audio`): `m4a` · `mp3`  
Playlist URLs (`youtube.com/playlist?list=…`): preview lists up to 30 entries; download at most 5 selected videos as separate jobs. Watch URLs with `&list=` stay single-video.

---



## Public launch checklist

1. Set a strong `DOWNLOAD_SIGNING_SECRET` in `.env`
2. Enable Turnstile: `CAPTCHA_ENABLED=true`, set `CAPTCHA_SECRET` / `CAPTCHA_SITE_KEY` and matching `NEXT_PUBLIC_*` values, then rebuild frontend
3. Put TLS in front of Nginx (see `nginx/tls.example.conf`)
4. Confirm rate limits and disk caps in `.env`

---



## Ops

```powershell
cd C:\Users\User\Desktop\Duktoo\YT

# Health
curl.exe http://localhost/api/health

# Redis queue depth (Celery default queue)
docker compose exec redis redis-cli LLEN celery

# Or with Redis-only container during local terminal dev
docker exec yt-redis redis-cli LLEN celery

# Concurrency smoke test (expects some 429 under per-IP caps)
py scripts\load_test.py --base http://localhost --n 20
```

Unix/Git Bash monitor helper:

```bash
sh scripts/monitor_check.sh http://localhost
```

Tune `CELERY_CONCURRENCY` (start at 1–2) before scaling hardware.