FROM python:3.11-slim

WORKDIR /app/gateway

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

COPY gateway/requirements.txt /app/gateway/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY gateway /app/gateway

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]
