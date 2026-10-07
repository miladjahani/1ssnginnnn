FROM python:3.12-slim-bookworm

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    HOST=0.0.0.0

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

# Expose port
EXPOSE 8000

# Direct command execution matching Railway & StanNG standard
CMD ["python3", "main.py"]
