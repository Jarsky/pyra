#!/bin/sh
set -e

DATA_DIR="${DATA_DIR:-/data}"
CONFIG_FILE="${CONFIG_FILE:-${DATA_DIR}/config.yaml}"
DB_URL="${DATABASE_URL:-sqlite+aiosqlite:///${DATA_DIR}/pyra.db}"
export CONFIG_FILE

# Bootstrap config.yaml from the bundled example if it doesn't exist yet.
if [ ! -f "${CONFIG_FILE}" ]; then
    echo "[entrypoint] ${CONFIG_FILE} not found — copying from config.example.yaml"
    mkdir -p "$(dirname "${CONFIG_FILE}")"
    cp /app/config.example.yaml "${CONFIG_FILE}"
    echo "[entrypoint] Created ${CONFIG_FILE}. Edit it to configure your bot."
fi

# Run DB migrations
echo "[entrypoint] Running database migrations..."
alembic upgrade head

# Launch bot
echo "[entrypoint] Starting Pyra..."
exec pybot --config "${CONFIG_FILE}" "$@"
