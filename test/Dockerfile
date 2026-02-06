# Dockerfile for retrace test runner
# Source code is mounted at runtime, so changes are picked up without rebuilding.
# Only rebuild if test/requirements.txt changes.

FROM python:3.11-slim

WORKDIR /app

# Install test dependencies (these rarely change)
COPY test/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
