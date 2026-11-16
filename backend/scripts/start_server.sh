#!/bin/sh
set -eu

if [ "${RUN_DB_MIGRATIONS_ON_STARTUP:-true}" = "true" ]; then
  alembic upgrade head
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}"
