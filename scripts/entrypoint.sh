#!/bin/sh
set -eu

python -m ai_greenhouse.infrastructure.database.wait
alembic upgrade head

if [ "$#" -eq 0 ]; then
    set -- uvicorn ai_greenhouse.app:create_app \
        --factory \
        --host "${APP_HOST}" \
        --port "${APP_PORT}" \
        --no-access-log
fi

exec "$@"

