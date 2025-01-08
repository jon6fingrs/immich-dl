
# Immich Downloader

Immich Downloader is a flexible script to download images from an Immich server. It allows users to specify configurations via YAML, environment variables, or command-line arguments. The script supports validation, safety checks, and can be run on bare metal or as a Docker container. You likely need to set up someway to schedule this.

---

## Why?

My entertainment center is run on a kodi box. I have wanted for a long time to set up a screensaver that would show photos of my family from my Immich library, taking advantage of its awesome facial recognition. I have been using Immich Kiosk on tablets which does this very well, but could not get it or anything like it setup as a screensaver on Kodi. Kodi does have a built in picture slideshow screensaver, however, which can be pointed at a local folder. I needed a way to import photos to that folder per person or album. That is the purpose of this script. I did not want to download every photo available to avoid A) using up disk space and B) because I want the most recent photos to seamlessly be inserted into the slideshow. Hence, this script. I have it set up to run every few hours and to download 200 photos at random. Those photos are seen from the slideshow and after another few hours theres a different set with as much a chance of seeing a recent photo as a very old one. That is why the script is not intended to perfectly mirror a person id or album id and why the old photos are deleted with each run. I am sure there could be other uses for this script, but this is why I created it and it has so far worked very well for me.

---

## Features

- Download images by **person ID**, **album ID**, or from the general pool.
- Validate downloaded images for resolution, aspect ratio, screenshots
- Safety checks for the download directory.
- Configurable via YAML, environment variables, or command-line flags.
- Log rotation for efficient debugging.
- Asyncio for a significant speed improvement

---

## Running on Bare Metal

1. Clone the repository:

   ```bash
   git clone https://github.com/jon6fingrs/immich-dl.git
   cd immich-dl
   ```
2. Install System Dependencies:

   Before installing Python dependencies, make sure you have the required system libraries:

   ```bash

   sudo apt-get update && sudo apt-get install -y \
       libjpeg-dev \
       zlib1g-dev \
       libheif-dev \
       libheif-examples \
       imagemagick \
       libimage-exiftool-perl \
       python3-pip
   ```

4. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Create a configuration file `config.yaml` (see example below).

6. Run the script:

   ```bash
   python3 immich-dl.py
   ```

7. You can override any configuration via environment variables or command-line flags.

---

## Running with Docker

### Using the Prebuilt Docker Image

The prebuilt image is available on Docker Hub: [thehelpfulidiot/immich-dl](https://hub.docker.com/repository/docker/thehelpfulidiot/immich-dl/general).

#### Example `docker run` Command

```bash
docker run --rm \
  -e IMMICH_URL=http://your-immich-instance-url \
  -e API_KEY=your-api-key-here \
  -e OUTPUT_DIR=/downloads \
  -e TOTAL_IMAGES_TO_DOWNLOAD=5 \
  -e PERSON_IDS='["person-id-1", "person-id-2"]' \
  -e ALBUM_IDS='["album-id-1", "album-id-2"]' \
  -e MIN_MEGAPIXELS=2.0 \
  -e MIN_WIDTH=1000 \
  -e MIN_HEIGHT=800 \
  -e SCREENSHOT_DIMENSIONS='[[1170, 2532], [1920, 1080]]' \
  -e MAX_PARALLEL_DOWNLOADS=5 \
  -e OVERRIDE=true \
  -e DRY_RUN=false \
  -e ENABLE_HEIC_CONVERSION=true \
  -v ./downloads:/downloads \
  thehelpfulidiot/immich-dl:latest
```

#### Example `docker-compose.yaml`

```yaml
version: "3.9"
services:
  immich-dl:
    image: thehelpfulidiot/immich-dl:latest
    container_name: immich-downloader
    environment:
      IMMICH_URL: "http://your-immich-instance-url"
      API_KEY: "your-api-key-here"
      OUTPUT_DIR: "/downloads" #env set to this in dockerfile
      TOTAL_IMAGES_TO_DOWNLOAD: "5"
      PERSON_IDS: '["person-id-1", "person-id-2"]'
      ALBUM_IDS: '["album-id-1", "album-id-2"]'
      MIN_MEGAPIXELS: "2.0" #optional, no default
      MIN_WIDTH: 1000 #optional, no default
      MIN_HEIGHT: 800 #optional, no default
      SCREENSHOT_DIMENSIONS: '[[1170, 2532], [1920, 1080]]'
      MAX_PARALLEL_DOWNLOADS: "5"
      OVERRIDE: "true"
      DRY_RUN: "false"
      ENABLE_HEIC_CONVERSION: "true"
      MIN_DATE: "2020-01-01" #optional, Minimum date in YYYY-MM-DD format
      MAX_DATE: "2025-01-01" #optional, Maximum date in YYYY-MM-DD format
    volumes:
      - ./downloads:/downloads
```

---

## Building the Docker Image Locally

1. Build the image:

   ```bash
   docker build -t immich-dl:latest .
   ```

2. Run the container:

   ```bash
   docker run --rm \
     -e IMMICH_URL=http://10.0.0.181:2283 \
     -e API_KEY=<your-api-key> \
     -e PERSON_IDS='["person-id-1","person-id-2"]' \
     -e TOTAL_IMAGES_TO_DOWNLOAD=5 \
     -v /path/to/downloads:/downloads \
     immich-dl:latest
   ```

---

## Configuration Options

The script supports configurations via **YAML**, **environment variables**, or **command-line arguments**. Below is a comprehensive table of options:

| **Option**                 | **Environment Variable**      | **YAML Key**                | **Command-Line Flag**    | **Description**                                                                                                                                                             |
|----------------------------|-------------------------------|-----------------------------|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Immich Server URL          | `IMMICH_URL`                 | `immich_url`               | N/A                      | Base URL of the Immich instance.                                                                                                                                       |
| API Key                    | `API_KEY`                    | `api_key`                  | N/A                      | API key for authentication.                                                                                                                                            |
| Output Directory           | `OUTPUT_DIR`                 | `output_dir`               | `--output-dir`           | Directory where images will be downloaded.                                                                                                                             |
| Total Images to Download   | `TOTAL_IMAGES_TO_DOWNLOAD`   | `total_images_to_download` | N/A                      | Number of images to download per person or album.                                                                                                                      |
| Person IDs                 | `PERSON_IDS`                 | `person_ids`               | N/A                      | JSON list of person IDs.                                                                                                                                               |
| Album IDs                  | `ALBUM_IDS`                  | `album_ids`                | N/A                      | JSON list of album IDs.                                                                                                                                                |
| Minimum Megapixels         | `MIN_MEGAPIXELS`             | `min_megapixels`           | N/A                      | Minimum megapixels for images to be downloaded.                                                                                                                       |
| Minimum Width              | `MIN_WIDTH`                  | `min_width`                | N/A                      | Minimum width for images (after orientation corrected)                                                                                                                |
| Minimum Height             | `MIN_HEIGHT`                 | `min_height`               | N/A                      | Minimum height for images (after orientation corrected)                                                                                                               |
| Screenshot Dimensions      | `SCREENSHOT_DIMENSIONS`      | `screenshot_dimensions`    | N/A                      | JSON list of dimensions to exclude (e.g., screenshots).                                                                                                               |
| Max Parallel Downloads     | `MAX_PARALLEL_DOWNLOADS`     | `max_parallel_downloads`   | N/A                      | Maximum number of parallel downloads.                                                                                                                                 |
| Override Safety Check      | `OVERRIDE`                  | `override`                 | `--override`             | Override safety checks for the directory.                                                                                                                             |
| Dry Run                    | `DRY_RUN`                   | `dry_run`                  | N/A                      | Simulate downloads without saving files.                                                                                                                              |
| Enable HEIC Conversion     | `ENABLE_HEIC_CONVERSION`    | `enable_heic_conversion`   | N/A                      | Convert HEIC to JPEG. Defaults to true.                                                                                                                               |
| Minimum Date               | `MIN_DATE`                  | `min_date`                 | N/A                      | Set minimum date for photo based off EXIF data.                                                                                                                       |
| Maximum Date               | `MAX_DATE`                  | `max_date`                 | N/A                      | Set maximum date for photo based off EXIF data.                                                                                                                       |

---

## Configuration Example: `config.yaml`

```yaml
# URL of your Immich instance
immich_url: "http://your-immich-instance-url"

# API Key for authentication
api_key: "your-api-key"

# Directory to save downloaded images
output_dir: "/path/to/downloads"

# Number of images to download per person/album
total_images_to_download: 10

# List of Person IDs (optional)
person_ids: []

# List of Album IDs (optional)
album_ids: []

# Minimum megapixels for images (optional)
min_megapixels: 2.0

# Minimum dimensions for images
min_width: 1024      # Set to null or remove this line to disable the width check
min_height: 768      # Set to null or remove this line to disable the height check

# Screenshot dimensions to exclude (optional)
screenshot_dimensions:
  - [1170, 2532]  # iPhone screenshot
  - [1920, 1080]  # Desktop screenshot

# Maximum parallel downloads (optional)
max_parallel_downloads: 5

# Override safety check for the directory (optional)
override: false

# Dry-run mode (optional)
dry_run: false

# Convert HEIC to JPEG (optional)
enable_heic_conversion: true

# Can add min or max dates. Will check based off EXIF data, first the date taken, or second, if unavailable, the date created. (optional)
min_date: "2020-01-01"  # Minimum date in YYYY-MM-DD format
max_date: "2025-01-01"  # Maximum date in YYYY-MM-DD format
```

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
