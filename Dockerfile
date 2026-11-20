# syntax=docker/dockerfile:1
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HOME=/home/app \
    ROOMS_DB=/data/rooms.db
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY rooms/ ./rooms/
RUN groupadd -r app && useradd -r -g app -d /home/app -m app \
    && mkdir -p /data && chown app:app /data
# Declared after the chown so a fresh named volume inherits app ownership.
VOLUME /data
USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
CMD ["python", "-W", "ignore::DeprecationWarning", "-m", "rooms.server"]
