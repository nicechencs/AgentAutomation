FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY ui ./ui
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 runner \
    && mkdir -p /data \
    && chown runner:runner /data
USER runner
EXPOSE 8000
CMD ["uvicorn", "agentautomation.app:app", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8000"]
