FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY infer_hub ./infer_hub
COPY scripts ./scripts

ENV CPU_LIMIT_PCT=70
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["uvicorn", "infer_hub.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
