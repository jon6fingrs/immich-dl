# Immich Downloader

The **Immich Downloader** script is a utility for downloading images from an Immich instance. It supports various configuration options for filtering, validating, and managing downloaded images. The script is highly customizable and can run on bare metal or within a Docker container.

---

## Features

- Download images from albums or people in an Immich instance.
- Supports filters such as minimum megapixels, aspect ratio, and dimensions.
- Detects and excludes screenshots based on specified dimensions.
- Corrects image orientation and converts HEIC files to JPG.
- Respects safety mechanisms to prevent unintended directory clearing.
- Logs detailed information with built-in log rotation.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Configuration](#configuration)
3. [Running the Script](#running-the-script)
   - [On Bare Metal](#on-bare-metal)
   - [Using Docker](#using-docker)
4. [Environment Variables](#environment-variables)
5. [Log Rotation](#log-rotation)
6. [Safety Mechanisms](#safety-mechanisms)
7. [Examples](#examples)

---

## Prerequisites

- Python 3.8 or higher
- Dependencies: Install via `pip install -r requirements.txt`
- Access to an Immich instance and a valid API key

---

## Configuration

The script uses a `config.yaml` file for primary configuration. All options in the YAML file can also be overridden using environment variables.

### `config.yaml` Example
```yaml
immich_url: "http://your-immich-instance-url"
api_key: "your-immich-api-key"
output_dir: "/path/to/output/directory"
total_images_to_download: 10
person_ids: []
album_ids: []
min_megapixels: 2.0
screenshot_dimensions:
  - [1170, 2532]
  - [1920, 1080]
dry_run: false
max_parallel_downloads: 5
disable_safety_check: false
override: false
```

---

## Running the Script

### On Bare Metal

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Script**:
   ```bash
   python3 immich-dl.py
   ```

3. **Optional Arguments**:
   - `--output-dir`: Specify a custom output directory.
   - `--override`: Temporarily bypass the safety check for the current run.

### Using Docker

1. **Build the Docker Image**:
   ```bash
   docker build -t immich-downloader .
   ```

2. **Run the Container**:
   ```bash
   docker run --rm -v /path/to/photos:/downloads      -e IMMICH_URL=http://your-immich-instance-url      -e API_KEY=your-api-key      immich-downloader
   ```

3. **Optional Environment Variables**:
   - See [Environment Variables](#environment-variables) for all configurable options.

4. **Using Docker Compose**:
   Create a `docker-compose.yml`:
   ```yaml
   version: "3.8"
   services:
     immich-downloader:
       image: immich-downloader
       environment:
         IMMICH_URL: http://your-immich-instance-url
         API_KEY: your-api-key
         TOTAL_IMAGES_TO_DOWNLOAD: 10
         OUTPUT_DIR: /downloads
         OVERRIDE: "false"
         DISABLE_SAFETY_CHECK: "false"
       volumes:
         - ./photos:/downloads
   ```
   Run the container:
   ```bash
   docker-compose up
   ```

---

## Environment Variables

| Environment Variable          | Description                                          | Default (if not set)           |
|--------------------------------|------------------------------------------------------|---------------------------------|
| `IMMICH_URL`                   | URL of the Immich instance                           | Must be specified              |
| `API_KEY`                      | API key for Immich authentication                   | Must be specified              |
| `OUTPUT_DIR`                   | Directory to save downloaded images                 | Specified in `config.yaml`     |
| `TOTAL_IMAGES_TO_DOWNLOAD`     | Number of images to download per person or album     | Specified in `config.yaml`     |
| `PERSON_IDS`                   | List of person IDs (JSON array)                     | Specified in `config.yaml`     |
| `ALBUM_IDS`                    | List of album IDs (JSON array)                      | Specified in `config.yaml`     |
| `MIN_MEGAPIXELS`               | Minimum megapixels for downloaded images            | Specified in `config.yaml`     |
| `SCREENSHOT_DIMENSIONS`        | Dimensions to exclude as screenshots (JSON array)   | Specified in `config.yaml`     |
| `DRY_RUN`                      | Enable dry-run mode (`true` or `false`)             | `false`                        |
| `MAX_PARALLEL_DOWNLOADS`       | Maximum number of concurrent downloads              | `5`                            |
| `DISABLE_SAFETY_CHECK`         | Disable safety check for the output directory       | `false`                        |
| `OVERRIDE`                     | Temporarily override safety checks for the run      | `false`                        |

---

## Log Rotation

The script automatically rotates logs to prevent excessive file sizes. By default:
- Logs are stored in `immich_downloader.log`.
- When the log file exceeds 5MB, it is rotated, and old logs are archived with timestamps.

---

## Safety Mechanisms

The script includes safety features to prevent accidental deletion of directories:

1. **Marker File**: A `.script_marker` file is created in the output directory.
2. **Safety Check**:
   - If the marker file is absent, the directory will not be cleared unless `override` is enabled.
   - Use `disable_safety_check` to bypass this check permanently.

---

## Examples

### Download Images for Specific People
```yaml
person_ids:
  - "person-id-1"
  - "person-id-2"
```
Run:
```bash
python3 immich-dl.py
```

### Temporarily Override Safety Checks
```bash
python3 immich-dl.py --override
```

### Use Environment Variables for Configuration
```bash
IMMICH_URL=http://your-immich-instance-url API_KEY=your-api-key TOTAL_IMAGES_TO_DOWNLOAD=5 python3 immich-dl.py
```

---

## Contributions

Contributions are welcome! Feel free to open issues or submit pull requests to improve the script.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
