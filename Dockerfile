FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PANTREE_DB_PATH=/var/lib/pantree/pantree_app.db

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && groupadd --system --gid 10001 pantree \
    && useradd --system --uid 10001 --gid pantree --home-dir /app pantree \
    && mkdir -p /var/lib/pantree \
    && chown -R pantree:pantree /app /var/lib/pantree

COPY --chown=pantree:pantree webapp ./webapp
COPY --chown=pantree:pantree recommendations ./recommendations
RUN mkdir -p /app/recommendations/.cache \
    && chown -R pantree:pantree /app/recommendations/.cache

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=172.31.0.2"]
