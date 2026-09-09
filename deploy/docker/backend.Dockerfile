FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app

WORKDIR /app
COPY requirements.lock.txt /app/requirements.lock.txt
RUN grep -v '^pywin32==' requirements.lock.txt > /tmp/runtime-requirements.txt \
    && pip install --no-cache-dir -r /tmp/runtime-requirements.txt
COPY . /app

RUN python -c "from enterprise_agent.api import app; assert app.title"
