# Official Playwright Python image — Chromium + all system libs pre-installed
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Unbuffered logs so Railway shows output in real time
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python deps (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Confirm Chromium is installed (already in the base image)
RUN playwright install chromium

# Copy project files
COPY . .

# ✅ FIX: filename is main.py (no spaces — Railway can't handle "main (1).py")
CMD ["python", "main.py"]
