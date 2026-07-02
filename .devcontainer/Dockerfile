FROM golang:1.25-bookworm AS go

FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

COPY --from=go /usr/local/go /usr/local/go

ENV DEBIAN_FRONTEND=noninteractive \
    GOTOOLCHAIN=local \
    PATH="/usr/local/go/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ninja-build \
        pkg-config \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    meson \
    meson-python \
    ninja \
    pytest \
    pytest-asyncio \
    setuptools_scm
