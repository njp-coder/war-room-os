# The War Room API: one long-running process. It keeps background threads (monitoring, Moss sync, repo sync,
# Vercel log streams) and SQLite on disk, so it runs as a single worker with a mounted volume at WARROOM_DATA_DIR.
FROM python:3.12-slim

# git: repos are cloned to read code. The rest are build inputs for the database drivers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential freetds-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 WARROOM_DATA_DIR=/data

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY config config

EXPOSE 8010
# Railway sets PORT. One worker: the background threads and the SQLite file must not be duplicated.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8010} --workers 1"]
