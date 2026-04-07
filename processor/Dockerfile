# processor/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# System dependencies for PyMuPDF and pdfplumber
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libmupdf-dev \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY processor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY processor/ /app/processor/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check: processor exposes a tiny HTTP health endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

ENTRYPOINT ["python", "-m", "processor.main"]