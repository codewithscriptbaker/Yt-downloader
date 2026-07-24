# Media Downloader Web App — Architecture & Build Spec

## 1. Overview

A web application that lets users paste a link to a video/media page (YouTube, and
other social platforms) and download the media file. Built for a small user base
(hundreds of concurrent users, not thousands), so the design favors simplicity and
low operational overhead over horizontal scalability.

**Core user flow:**
1. User pastes a URL into the frontend.
2. Backend validates the URL and creates a download job.
3. A background worker extracts and downloads the media using `yt-dlp`.
4. Frontend polls (or subscribes to) job status until it's done.
5. User gets a download link; the file is served and later auto-deleted.

---

## 2. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | **Next.js** (React, TypeScript) | UI, polling/status display, download trigger |
| Backend API | **FastAPI** (Python) | Validation, job creation, status endpoints |
| Extraction engine | **yt-dlp** | Actual download/extraction logic, runs inside worker |
| Job queue | **Redis** + **Celery** (or `rq` as a lighter alternative) | Decouples slow downloads from the request/response cycle |
| Storage | Local disk (small scale) — abstracted so it can move to S3-compatible storage later | Temporary files only, auto-expired |
| Reverse proxy | **Nginx** | TLS termination, routing, basic rate limiting |
| Database (optional) | **SQLite** or **Postgres** | Only needed if we want job history / accounts; otherwise Redis holds ephemeral state |
| Deployment | **Docker Compose**, single VM | No Kubernetes needed at this scale |

---

## 3. High-Level Architecture

```
[ Next.js Frontend ]
        |
        v  (HTTPS via Nginx)
[ FastAPI Backend ]  --validate & enqueue-->  [ Redis Queue + Status Store ]
        ^                                              |
        |                                              v
        |                                   [ Celery Worker running yt-dlp ]
        |                                              |
        +------------- download link ------------------[ File Storage (temp, auto-expire) ]
```

**Flow in words:**
- Frontend submits a URL to `POST /api/jobs`.
- FastAPI validates the URL (format, allowed domains, not already queued abusively), creates a job record, pushes it onto the Redis-backed queue, and returns a `job_id` immediately (non-blocking).
- A Celery worker (separate process/container) pulls the job, runs `yt-dlp` against the URL, and updates job status in Redis at each stage (`queued → downloading → processing → done` or `failed`).
- Frontend polls `GET /api/jobs/{job_id}` (or uses a WebSocket/SSE channel) to show progress.
- On completion, the API returns a signed, time-limited download URL pointing at the file in storage.
- A cleanup job (cron or Celery beat task) deletes files and job records past a TTL (e.g., 1 hour).

---

## 4. Backend (FastAPI) — Responsibilities & Endpoints

### Responsibilities
- Input validation (accepted URL patterns/domains, reject malformed input)
- Rate limiting per IP/user (critical — this type of endpoint gets abused)
- Job creation and enqueueing (never call `yt-dlp` synchronously in a request handler)
- Status/progress reporting
- Serving/redirecting to the finished file (signed URL, not a raw open file path)
- Error handling and surfacing clear failure reasons (private video, geo-blocked, invalid link, unsupported site, etc.)

### Suggested Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Accepts `{ "url": "..." }`, validates, creates job, returns `{ "job_id": "..." }` |
| `GET` | `/api/jobs/{job_id}` | Returns `{ "status": "queued/downloading/done/failed", "progress": 0-100, "error": null }` |
| `GET` | `/api/jobs/{job_id}/download` | Returns a signed download URL or streams the file, only when status is `done` |
| `DELETE` | `/api/jobs/{job_id}` | (Optional) Lets a user cancel/remove their own job |
| `GET` | `/api/health` | Health check for uptime monitoring |
| `WS` | `/ws/jobs/{job_id}` | (Optional) Push-based status updates instead of polling |

### Validation Rules to Implement
- Only accept URLs from an explicit allow-list of supported domains.
- Reject obviously malformed input before touching the queue.
- Cap max simultaneous jobs per IP/session (e.g., 2 active jobs at once).
- Cap job submissions per IP per time window (e.g., 10/hour) — use `slowapi` or Nginx-level limiting.
- Set a max file size / duration limit to avoid one job consuming all disk space.

---

## 5. Worker (Celery + yt-dlp) — Responsibilities

- Pull jobs off the Redis queue.
- Run `yt-dlp` with a locked-down config (specific format selection, output path template, no arbitrary shell execution from user input).
- Update job progress in Redis as `yt-dlp` reports download percentage (via its progress hooks).
- On success: move/rename the final file into the storage directory, update job status to `done`, store file path + expiry time.
- On failure: capture the actual `yt-dlp` error, map it to a user-friendly message, set status to `failed`.
- Enforce timeouts — kill jobs that run too long (e.g., > 10 minutes).
- Run as a separate container/process from the API so a stuck download never blocks web traffic.

**Scaling note:** start with 1–2 worker processes/concurrency slots. At "few hundred users" scale, this is likely enough; increase Celery concurrency before adding more infrastructure.

---

## 6. Storage & Cleanup

- Store finished files in a dedicated directory (not web-root accessible directly).
- Serve files only through an authenticated/signed endpoint — never expose a raw static file path tied to a predictable job ID.
- Attach a TTL to every file when it's created (e.g., 1 hour from completion).
- Run a periodic cleanup task (Celery beat, or a simple cron script) that:
  - Deletes files past their TTL.
  - Deletes/expires the corresponding Redis job record.
- Track total disk usage; if nearing a cap, temporarily reject new jobs with a clear error rather than filling the disk.

---

## 7. Frontend (Next.js) — Responsibilities

- Simple input form: paste URL, submit.
- Call `POST /api/jobs`, store returned `job_id`.
- Poll `GET /api/jobs/{job_id}` on an interval (e.g., every 2 seconds) or open a WebSocket — show a progress bar/spinner and status text.
- On `done`, show a download button pointing at the signed download URL.
- On `failed`, show the specific error message returned by the API (not a generic "something went wrong").
- Basic client-side validation (URL looks like a URL) before hitting the API, to reduce obviously-bad requests.
- No sensitive logic on the client — all validation/rate-limiting is enforced server-side too.

---

## 8. Infrastructure & Deployment

- **Docker Compose services:**
  - `frontend` — Next.js app
  - `backend` — FastAPI app
  - `worker` — Celery worker(s) running `yt-dlp`
  - `redis` — queue + status store
  - `nginx` — reverse proxy, TLS termination, routes `/` to frontend and `/api/*` + `/ws/*` to backend
  - `beat` (optional) — Celery beat for scheduled cleanup
- Single VM is sufficient at target scale. Keep services stateless where possible so moving to multiple machines later isn't a rewrite.
- Environment variables to define: `REDIS_URL`, `STORAGE_PATH`, `FILE_TTL_SECONDS`, `MAX_JOBS_PER_IP`, `ALLOWED_DOMAINS`, `MAX_FILE_SIZE_MB`, `JOB_TIMEOUT_SECONDS`.
- Logging: structured logs from both API and worker (job id, url domain, status, duration, error) to make debugging failed downloads easy.
- Monitoring (lightweight, at this scale): basic uptime checks on `/api/health`, disk usage alert, and queue length alert (if jobs pile up, something's stuck).

---

## 9. Security & Abuse Prevention

- Rate limit per IP at both Nginx and application level.
- Domain allow-list — don't accept arbitrary URLs to arbitrary sites.
- Never pass raw user input into a shell command — use `yt-dlp`'s Python API or subprocess with explicit argument arrays (never `shell=True` with interpolated strings).
- Signed, expiring download links — don't expose predictable `/files/{job_id}.mp4` paths.
- File size / duration caps to prevent disk exhaustion.
- Job timeouts to prevent one hung job consuming a worker slot indefinitely.
- Consider CAPTCHA or simple bot-check on submission if abuse becomes an issue.

---

## 10. Legal / Terms of Service Note

Downloading from platforms like YouTube generally conflicts with those platforms' Terms of Service. This is a policy/legal consideration separate from the technical build — worth deciding up front how public-facing the tool will be, whether it's for personal/internal use, and whether any disclaimers or usage restrictions should be shown in the UI.

---

## 11. Suggested Build Order

1. FastAPI skeleton: `/api/health`, `POST /api/jobs` (stub, no worker yet), in-memory or Redis job store.
2. Wire up Redis + Celery, get a worker running `yt-dlp` end-to-end for one hardcoded URL.
3. Connect job creation → queue → worker → status update loop.
4. Build Next.js frontend: submit form + polling UI against the real API.
5. Add signed download endpoint + file serving.
6. Add cleanup task (TTL-based file/job deletion).
7. Add rate limiting, domain allow-list, input validation hardening.
8. Dockerize all services, wire up Nginx, deploy to a single VM.
9. Add logging/monitoring basics.
10. Load-test with a few dozen concurrent submissions to confirm worker concurrency is sufficient.

---

## 12. Open Decisions for the Team

- Polling vs WebSocket for status updates (WebSocket is nicer UX, polling is simpler to build first).
- Anonymous use vs lightweight accounts (affects rate limiting granularity and job history).
- Which platforms/domains to officially support at launch (affects the allow-list and `yt-dlp` config).
- File TTL duration (balance user convenience vs disk usage).
