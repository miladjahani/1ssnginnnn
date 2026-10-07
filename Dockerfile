FROM python:3.12-slim-bookworm

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    DATA_DIR=/data

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Ensure scripts are executable
RUN chmod +x *.sh 2>/dev/null || true

# Persistent storage mount point (attach a Railway Volume here to keep data)
RUN mkdir -p /data

# Expose port
EXPOSE 8000

# Liveness probe against the real /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/health\", timeout=4)" || exit 1

# Direct command execution matching Railway & StanNG standard
CMD ["python3", "main.py"]
