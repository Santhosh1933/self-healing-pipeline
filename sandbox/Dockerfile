FROM python:3.12-slim-bookworm

ARG PYSPARK_VERSION=3.5.6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y openjdk-17-jre-headless git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "pytest>=8.3,<9" "pyspark==${PYSPARK_VERSION}"

COPY validate.py /opt/autoheal/validate.py

WORKDIR /workspace
ENTRYPOINT ["python", "/opt/autoheal/validate.py"]
