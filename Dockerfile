# Use Python base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set default environment variables
ENV OUTPUT_DIR="/downloads"
ENV TOTAL_IMAGES_TO_DOWNLOAD=10
ENV OVERRIDE=false
ENV MAX_PARALLEL_DOWNLOADS=5
ENV DRY_RUN=false

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    libjpeg-dev \
    zlib1g-dev \
    libheif-examples \ 
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure output directory exists
RUN mkdir -p /downloads

# Set default command
CMD ["python3", "immich-dl.py"]
