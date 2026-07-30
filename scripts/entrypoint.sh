#!/bin/sh
set -eu

python -m ai_greenhouse.infrastructure.database.wait
alembic upgrade head

# With no command, this container is the local demonstration: prepare the growbox
# the dashboard expects, then serve it. `demo-init` is a command of its own rather
# than an application startup hook, and it is idempotent, so a second
# `docker compose up` finds what the first one created.
#
# An explicit command — `pytest`, `alembic`, a shell — runs exactly that and seeds
# nothing. A test suite that quietly acquired a growbox would be asserting against
# a database no test had written.
#
# The environment guard keeps demonstration data out of anything that is not the
# local demo. Deploying this image is out of scope for 0.1.
if [ "$#" -eq 0 ]; then
    if [ "${APP_ENV:-local}" = "local" ]; then
        python -m ai_greenhouse.seed demo-init
    fi

    set -- uvicorn ai_greenhouse.app:create_app \
        --factory \
        --host "${APP_HOST}" \
        --port "${APP_PORT}" \
        --no-access-log
fi

exec "$@"
