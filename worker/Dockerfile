FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies required by playwright install-deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (includes playwright)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium system deps and the browser binary
RUN playwright install-deps chromium \
    && playwright install chromium

# Copy the worker code
COPY . worker/

CMD ["python", "-m", "worker.main"]
