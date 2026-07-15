FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

WORKDIR /app/pipeline/server
EXPOSE 8790

CMD ["sh", "-c", "cd /app/pipeline && uvicorn server.main:app --host 0.0.0.0 --port 8790"]
