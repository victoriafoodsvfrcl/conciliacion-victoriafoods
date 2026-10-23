# Imagen del Cloud Run Job: conciliacion run-all (sync SharePoint -> GCS -> BigQuery raw -> Dataform)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias primero (mejor cache de capas)
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[gcp]"

# Usuario sin privilegios
RUN useradd --create-home --uid 10001 app
USER app

ENTRYPOINT ["conciliacion"]
CMD ["run-all", "--modo", "diario"]
