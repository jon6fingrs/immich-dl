
# Immich Downloader

Immich Downloader is a flexible script to download images from an Immich server. It allows users to specify configurations via YAML, environment variables, or command-line arguments. The script supports validation, safety checks, and can be run on bare metal or as a Docker container.

---

## Features

- Download images by **person ID**, **album ID**, or from the general pool.
- Validate downloaded images for resolution, aspect ratio, and avoid single-color images.
- Safety checks for the download directory.
- Configurable via YAML, environment variables, or command-line flags.
- Log rotation for efficient debugging.
- Asyncio and multithreading for a significant speed improvement

---

## Running on Bare Metal

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd <repository-folder>
   ```

2. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a configuration file `config.yaml` (see example below).

4. Run the script:

   ```bash
   python3 immich-dl.py
   ```

5. You can override any configuration via environment variables or command-line flags.

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
  -e SCREENSHOT_DIMENSIONS='[[1170, 2532], [1920, 1080]]' \
  -e MAX_PARALLEL_DOWNLOADS=5 \
  -e OVERRIDE=true \
  -e DISABLE_SAFETY_CHECK=false \
  -e DRY_RUN=false \
  -e ENABLE_HEIC_CONVERSION=true
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
      OUTPUT_DIR: "/downloads"
      TOTAL_IMAGES_TO_DOWNLOAD: "5"
      PERSON_IDS: '["person-id-1", "person-id-2"]'
      ALBUM_IDS: '["album-id-1", "album-id-2"]'
      MIN_MEGAPIXELS: "2.0"
      SCREENSHOT_DIMENSIONS: '[[1170, 2532], [1920, 1080]]'
      MAX_PARALLEL_DOWNLOADS: "5"
      OVERRIDE: "true"
      DISABLE_SAFETY_CHECK: "false"
      DRY_RUN: "false"
      ENABLE_HEIC_CONVERSION: "true"
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
| Screenshot Dimensions      | `SCREENSHOT_DIMENSIONS`      | `screenshot_dimensions`    | N/A                      | JSON list of dimensions to exclude (e.g., screenshots).                                                                                                               |
| Max Parallel Downloads     | `MAX_PARALLEL_DOWNLOADS`     | `max_parallel_downloads`   | N/A                      | Maximum number of parallel downloads.                                                                                                                                 |
| Disable Safety Check       | `DISABLE_SAFETY_CHECK`       | `disable_safety_check`     | N/A                      | Disable safety check for the directory marker.                                                                                                                        |
| Override Safety Check      | `OVERRIDE`                  | `override`                 | `--override`             | Override safety checks for the directory.                                                                                                                             |
| Dry Run                    | `DRY_RUN`                   | `dry_run`                  | N/A                      | Simulate downloads without saving files.                                                                                                                              |
| Enable HEIC Conversion     | `ENABLE_HEIC_CONVERSION`    | `enable_heic_conversion`   | N/A                      | Convert HEIC to JPEG. Defaults to true.                                                                                                                               |

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

# Screenshot dimensions to exclude (optional)
screenshot_dimensions:
  - [1170, 2532]  # iPhone screenshot
  - [1920, 1080]  # Desktop screenshot

# Maximum parallel downloads (optional)
max_parallel_downloads: 5

# Disable safety check for the directory (optional)
disable_safety_check: false

# Override safety check for the directory (optional)
override: false

# Dry-run mode (optional)
dry_run: false

# Convert HEIC to JPEG (optional)
enable_heic_conversion: true
```

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
