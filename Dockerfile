FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    DATABASE_URL=sqlite:////data/mlb_visualizer.db \
    PYTHONPATH=/app

WORKDIR /app

RUN pip install --no-cache-dir poetry==1.8.3

COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

# Dedicated volume mount point so persisted data survives container
# recreation without a bind mount clobbering the application code in /app.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# The entrypoint applies migrations (idempotent) before running whatever
# command was given, so overriding CMD to run an import script still lands
# on a migrated schema rather than skipping the migration step.
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
