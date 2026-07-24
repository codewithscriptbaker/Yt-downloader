#!/usr/bin/env sh
# Lightweight ops checks: health, disk usage, Redis queue depth.
# Usage: ./scripts/monitor_check.sh http://localhost

set -eu
BASE="${1:-http://localhost}"
REDIS_CONTAINER="${REDIS_CONTAINER:-yt-redis-1}"

echo "== health =="
curl -fsS "$BASE/api/health" | tee /tmp/md-health.json
echo

echo "== redis queue depth (celery) =="
if command -v docker >/dev/null 2>&1; then
  docker exec "$REDIS_CONTAINER" redis-cli LLEN celery 2>/dev/null || \
    docker compose exec -T redis redis-cli LLEN celery || true
else
  echo "docker not available; skip queue check"
fi

echo
echo "== tips =="
echo "- Alert if health.status != ok"
echo "- Alert if disk_usage_mb approaches disk_limit_mb"
echo "- Alert if LLEN celery stays high for > 10 minutes (stuck workers)"
