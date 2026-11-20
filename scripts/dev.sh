#!/usr/bin/env bash
# Run Curiox OS natively (no Docker): uses .env, but points Postgres/Redis at localhost.
# Needs: Postgres + Redis running locally (brew services run postgresql@14 redis), .venv, dashboard/node_modules.
set -euo pipefail
cd "$(dirname "$0")/.."

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
export DATABASE_URL="$(env_get DATABASE_URL | sed 's#@postgres:#@localhost:#')"
export REDIS_URL="$(env_get REDIS_URL | sed 's#//redis:#//localhost:#')"
export MEDIA_DIR="$PWD/media"
API_PORT="${API_PORT:-8010}"  # 8000 is often taken by other local apps
mkdir -p "$MEDIA_DIR" logs

.venv/bin/alembic upgrade head

trap 'kill 0' EXIT INT TERM  # stop everything on Ctrl-C
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" >logs/api.log 2>&1 &
.venv/bin/celery -A app.workers worker -l info --concurrency 2 >logs/worker.log 2>&1 &
.venv/bin/celery -A app.workers beat -l info --schedule logs/celerybeat-schedule >logs/beat.log 2>&1 &
(cd dashboard && API_URL="http://localhost:$API_PORT" npx vite --port 5173 --strictPort >../logs/dashboard.log 2>&1) &

echo "Curiox OS: http://localhost:5173  (API http://localhost:$API_PORT, logs in ./logs)"
wait
