FROM python:3.12-slim
WORKDIR /app
COPY src/python/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/python/app ./app
EXPOSE 8000
CMD ["sh", "-c", "PORT=\"${PORT:-8000}\"; if [ \"$AI_MTLS_REQUIRED\" = \"true\" ]; then exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --ssl-certfile \"$AI_MTLS_CERT_PATH\" --ssl-keyfile \"$AI_MTLS_KEY_PATH\" --ssl-ca-certs \"$AI_MTLS_CA_PATH\" --ssl-cert-reqs 2; else exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\"; fi"]
