FROM python:3.12-slim

WORKDIR /app

ENV OUTPUT_DIR="/downloads" \
    PYTHONUNBUFFERED=1

# exiftool writes the optional location captions. HEIC/AVIF decoding comes
# from the pillow-heif wheel, so no system image libraries are needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY immich-dl.py .

RUN mkdir -p /downloads

CMD ["python3", "immich-dl.py"]
