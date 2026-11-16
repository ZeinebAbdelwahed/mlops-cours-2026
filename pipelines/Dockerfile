FROM python:3.12-slim

WORKDIR /app

# Installer les dépendances
COPY pipelines/requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt \
    && pip install --no-cache-dir --root-user-action=ignore --no-deps kfp==2.17.0

# Copier le projet et le contrat Great Expectations
COPY dataset.yaml params.yaml ./
COPY src/ src/
COPY gx/ gx/
COPY tests/ tests/
COPY pipelines/__init__.py pipelines/stage.py pipelines/

# Variables d'environnement
ENV PYTHONPATH=/app \
    DATASET_CONFIG=/app/dataset.yaml \
    PYTHONUNBUFFERED=1 \
    DO_NOT_TRACK=true

ARG VERSION=dev

LABEL org.opencontainers.image.title="scoring-pipeline" \
      org.opencontainers.image.version="${VERSION}"
