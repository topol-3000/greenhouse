FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src ./src
RUN uv sync --frozen

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint
COPY tests ./tests

RUN chmod 0755 /usr/local/bin/entrypoint

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint"]

