FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY spatial_id ./spatial_id
COPY tests ./tests
COPY scripts ./scripts
RUN pip install --no-cache-dir '.[service,postgres,dev]' \
    && useradd --create-home --uid 10001 spatialid \
    && mkdir /data /results \
    && chown spatialid:spatialid /data /results

USER spatialid
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"

CMD ["uvicorn", "spatial_id.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "10", "--limit-concurrency", "64"]

