# ✅ Use the official Playwright Python image.
# It includes Chromium + ALL required system libraries pre-installed.
# No need to manually apt-get install dozens of libs.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Keep Python output unbuffered so logs appear in Railway instantly
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser (already available in the image, this just confirms it)
RUN playwright install chromium

# Copy the rest of your project
COPY . .

CMD ["python", "main.py"]
