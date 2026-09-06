# Commands

Repo root (PowerShell):

```powershell
cd "d:\Repo Projects\Yt\Yt-downloader"
```

---

## One-time setup

```powershell
copy .env.example .env

docker run -d --name yt-redis -p 6379:6379 redis:7-alpine

cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..

cd frontend
npm install
cd ..
```

---

## Run (recommended)

```powershell
py scripts\run_all.py
```

- Web: http://localhost:3005  
- API: http://localhost:8009  
- Docs: http://localhost:8009/docs  
- Stop: `Ctrl+C`

Phone (same Wi‑Fi): `http://<LAN-IP>:3005`

```powershell
ipconfig
```

---

## Redis

```powershell
docker ps -a --filter "name=redis"
docker start yt-redis
docker stop yt-redis
docker exec yt-redis redis-cli ping
Test-NetConnection localhost -Port 6379
```

---

## Firewall (phone LAN access — run as Admin once)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\allow_lan_firewall.ps1
```

---

## Run manually (4 terminals)

### API

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8009
```

### Celery worker

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
celery -A app.celery_app.celery_app worker --loglevel=INFO --concurrency=2 --pool=solo
```

### Celery beat

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
celery -A app.celery_app.celery_app beat --loglevel=INFO
```

### Frontend

```powershell
cd frontend
npm run dev -- -H 0.0.0.0 -p 3005
```

---

## Docker Compose (full stack)

```powershell
copy .env.example .env
docker compose up --build -d
docker compose logs -f
docker compose down
```

Open: http://localhost

---

## Tests

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="."
py tests\test_local.py
```
