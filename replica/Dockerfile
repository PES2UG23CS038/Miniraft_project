FROM python:3.11-slim

WORKDIR /app/replica

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

COPY replica/requirements.txt /app/replica/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
# Default source in image; compose bind mounts override at runtime.
COPY replica1 /app/replica

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${RPC_PORT:-5000} --reload"]
