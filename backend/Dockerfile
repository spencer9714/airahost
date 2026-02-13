# ── Base: Microsoft Playwright image with Chromium pre-installed ──
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only — saves ~400 MB)
RUN playwright install --with-deps chromium

# Copy application code
COPY main.py .

# Cloud Run injects PORT env var (default 8080)
ENV PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
