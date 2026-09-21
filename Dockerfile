FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    EMBEDDED_REDIS=1 \
    DEBUG=False

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    redis-server \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh \
    && python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["./docker-entrypoint.sh"]
