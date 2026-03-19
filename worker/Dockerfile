# Use the official Playwright Python image which includes necessary browser deps
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the specific browsers required by Playwright
RUN playwright install chromium

# Copy the worker code
# Copy current directory (worker context) into /app/worker so imports work
COPY . worker/

# Set the entrypoint to run the worker module
# This assumes the container will be run with access to the necessary environment variables
CMD ["python", "-m", "worker.main"]