FROM python:3.11-slim

# Install ffmpeg (and some cert/tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render provides $PORT
ENV PORT=10000
EXPOSE 10000

# Run with gunicorn in production
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120"]
