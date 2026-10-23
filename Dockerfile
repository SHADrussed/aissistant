FROM python:3.12-slim@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app
# Railway builder accepts only cache mounts; no build secrets are needed here.
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /tmp/uv-cache
COPY --chown=10001:10001 config/default.toml ./config/default.toml
COPY docker-entrypoint.py ./
RUN mkdir -p /data && chown 10001:10001 /data
# Entrypoint only prepares the volume, then irrevocably drops root before app startup.
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
CMD ["/app/.venv/bin/aissistant"]
