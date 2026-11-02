ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 180 --retries 10 --upgrade pip
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 180 --retries 10 torch==2.5.1 --index-url https://download.pytorch.org/whl/cu124
WORKDIR /workspace
