FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/api

# non-root runtime user
RUN groupadd -r cpo && useradd -r -g cpo cpo

COPY apps/api/pyproject.toml apps/api/README.md* ./
COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./
COPY workers ./workers

RUN pip install --upgrade pip && pip install .

# keep alembic + package importable from workdir
RUN chown -R cpo:cpo /srv/api
USER cpo

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
