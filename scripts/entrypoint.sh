#!/bin/sh
set -eu

python -m ai_greenhouse.infrastructure.database.wait
alembic upgrade head

# With no command, this container serves the API and nothing else. The cloud
# creates no topology, no configuration and no demonstration data of its own, so
# a fresh database stays empty until a client provisions it through the public
# HTTP APIs.
#
# An explicit command — `pytest`, `alembic`, a shell — runs exactly that.
if [ "$#" -eq 0 ]; then
    set -- uvicorn ai_greenhouse.app:create_app \
        --factory \
        --host "${APP_HOST}" \
        --port "${APP_PORT}" \
        --no-access-log
fi

exec "$@"
