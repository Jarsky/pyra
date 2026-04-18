#!/bin/sh
set -e

DATA_DIR="${DATA_DIR:-/data}"
CONFIG_FILE="${CONFIG_FILE:-${DATA_DIR}/config.yaml}"
DB_URL="${DATABASE_URL:-sqlite+aiosqlite:///${DATA_DIR}/pyra.db}"

# Run DB migrations
echo "[entrypoint] Running database migrations..."
alembic upgrade head

# Launch bot
echo "[entrypoint] Starting Pyra..."
exec pybot --config "${CONFIG_FILE}" "$@"
