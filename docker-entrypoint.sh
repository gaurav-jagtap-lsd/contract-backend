          #!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"
EMBEDDED_REDIS="${EMBEDDED_REDIS:-1}"

shutdown() {
  echo "Shutting down..."
  kill -TERM "${web_pid:-}" "${worker_pid:-}" "${beat_pid:-}" "${redis_pid:-}" 2>/dev/null || true
  wait || true
}
trap shutdown SIGTERM SIGINT

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "$EMBEDDED_REDIS" = "1" ]; then
  echo "Starting embedded Redis..."
  redis-server --save "" --appendonly no --bind 127.0.0.1 --port 6379 --daemonize no &
  redis_pid=$!
  export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
  for _ in $(seq 1 20); do
    if redis-cli ping >/dev/null 2>&1; then
      break
    fi
    sleep 0.25
  done
fi

echo "Starting Celery worker..."
celery -A celery_app worker --loglevel=info --concurrency="${CELERY_CONCURRENCY:-2}" &
worker_pid=$!

echo "Starting Celery beat..."
celery -A celery_app beat --loglevel=info &
beat_pid=$!

echo "Starting Gunicorn on 0.0.0.0:${PORT}..."
gunicorn contractvault_api.wsgi:application \
  --bind "0.0.0.0:${PORT}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout 120 &
web_pid=$!

wait "$web_pid"
