FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HIKKA_HUB_BIND=0.0.0.0 \
    HIKKA_HUB_PORT=8765 \
    HIKKA_HUB_DATABASE=/data/hikka-hub.sqlite3

RUN groupadd --system hikkahub \
    && useradd --system --gid hikkahub --home-dir /nonexistent hikkahub

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /app/hikka_hub
RUN mkdir -p /data && chown hikkahub:hikkahub /data

USER hikkahub
EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).read()"]

CMD ["python", "-m", "hikka_hub"]

