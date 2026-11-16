ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── runtime ──────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE}

WORKDIR /app

COPY --from=builder /install /usr/local

COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "scripts/start_server.sh"]
