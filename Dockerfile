FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WC_DB_PATH=/data/worldcup.db

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worldcup ./worldcup

# Run as non-root; /data is the mounted PVC.
RUN useradd --uid 10001 --create-home appuser \
 && mkdir -p /data && chown appuser:appuser /data
USER appuser

EXPOSE 8002

# Web server is the default. The poller CronJob overrides command with:
#   ["python","-m","worldcup.poll","poll"]  (or "seed")
CMD ["uvicorn", "worldcup.app:app", "--host", "0.0.0.0", "--port", "8002"]
