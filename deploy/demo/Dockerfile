ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.13-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY --from=builder /install /usr/local
COPY backend/ /app/
COPY miniapp/dist-h5/ /app/web/
COPY deploy/demo/start_demo.sh /app/start_demo.sh
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEMO_ENABLED=true DEMO_STATIC_DIR=/app/web
EXPOSE 8000
CMD ["sh", "start_demo.sh"]
