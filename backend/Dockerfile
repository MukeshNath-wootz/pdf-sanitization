FROM python:3.11-slim

# System packages needed by Tesseract and some Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend code
COPY . .

# Tesseract path + default port
ENV TESSERACT_CMD=tesseract
ENV PORT=10000

# IMPORTANT: use $PORT Render provides
CMD ["/bin/sh","-c","uvicorn api_app:app --host 0.0.0.0 --port ${PORT:-10000}"]
