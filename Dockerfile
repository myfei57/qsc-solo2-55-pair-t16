FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY . .

RUN mkdir -p var/state

EXPOSE 8080

CMD ["python", "-m", "cspplant", "--config", "config/csp.json", "serve", "--host", "0.0.0.0", "--port", "8080"]
