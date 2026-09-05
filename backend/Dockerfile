# Shared image for the `backend` (FastAPI) and `scheduler` (APScheduler +
# LISTEN/NOTIFY) docker-compose services (plan §9) -- both run the exact same
# codebase/dependencies, just a different entrypoint command, so one image
# serves both rather than maintaining two near-identical Dockerfiles.
FROM python:3.11-slim

# postgresql-client: only used by the `seed` service's entrypoint (psql -f
# schema.sql/api_schema.sql + real_data ingest) -- kept in this shared image rather
# than a separate one so docker-compose.yml can run the seed job with
# `image: backend` + a different command instead of building a 4th image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
