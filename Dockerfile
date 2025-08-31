
# Dockerfile - DQL Proxy
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8080
# Required env:
# - DT_URL (e.g., https://<tenant>.live.dynatrace.com/e/<env> )
# - DT_TOKEN (API token with query permissions)
# Optional:
# - ORG_BEARER (proxy bearer for incoming requests)
# - QUERY_TIMEOUT, RAW_TTL
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8080"]
