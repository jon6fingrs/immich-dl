import os
import requests
import logging
import random
from PIL import Image
import argparse
import yaml
import shutil
import json
from logging.handlers import RotatingFileHandler

# ------------------------------
# 1. Configuration and Logging
# ------------------------------

def load_config(config_file="config.yaml"):
    try:
        # Load configuration from YAML file
        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f) or {}  # Load YAML or default to empty dictionary
        except FileNotFoundError:
            logging.warning(f"Configuration file {config_file} not found. Proceeding with environment variables only.")
            config = {}

        # Populate configuration with environment variables or default values
        config["immich_url"] = os.getenv("IMMICH_URL", config.get("immich_url", ""))
        config["api_key"] = os.getenv("API_KEY", config.get("api_key", ""))
        config["output_dir"] = os.getenv("OUTPUT_DIR", config.get("output_dir", "/downloads"))
        config["total_images_to_download"] = int(
            os.getenv("TOTAL_IMAGES_TO_DOWNLOAD", config.get("total_images_to_download", 10))
        )
        config["person_ids"] = json.loads(
            os.getenv("PERSON_IDS", json.dumps(config.get("person_ids", [])))
        )
        config["album_ids"] = json.loads(
            os.getenv("ALBUM_IDS", json.dumps(config.get("album_ids", [])))
        )
        config["min_megapixels"] = float(
            os.getenv("MIN_MEGAPIXELS", config.get("min_megapixels", 0))
        )
        config["screenshot_dimensions"] = json.loads(
            os.getenv("SCREENSHOT_DIMENSIONS", json.dumps(config.get("screenshot_dimensions", [])))
        )
        config["override"] = os.getenv("OVERRIDE", str(config.get("override", False))).lower() in ["true", "1"]
        config["disable_safety_check"] = os.getenv("DISABLE_SAFETY_CHECK", str(config.get("disable_safety_check", False))).lower() in ["true", "1"]
        config["max_parallel_downloads"] = int(
            os.getenv("MAX_PARALLEL_DOWNLOADS", config.get("max_parallel_downloads", 5))
        )
        config["dry_run"] = os.getenv("DRY_RUN", str(config.get("dry_run", False))).lower() in ["true", "1"]

        # Validate essential keys
        if not config["immich_url"] or not config["api_key"]:
            raise ValueError("Both IMMICH_URL and API_KEY must be specified, either in the YAML file or as environment variables.")

        logging.info(f"Configuration loaded: {config}")  # Log the final config
        return config

    except (yaml.YAMLError, ValueError, json.JSONDecodeError) as e:
        logging.error(f"Error parsing config file or environment variables: {e}")
        raise





def setup_logging():
    """
    Configure logging with log rotation.
    """
    log_file = "immich_downloader.log"
    max_log_size = 5 * 1024 * 1024  # 5 MB
    backup_count = 3  # Keep 3 backup files

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Stream handler for console output
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(stream_handler)

    # Rotating file handler for log file
    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_log_size, backupCount=backup_count
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)


# ------------------------------
# 2. Directory Management
# ------------------------------

def check_and_clear_directory(directory, override):
    """
    Checks and clears the directory if the marker file is present.
    If the marker file is missing and files exist, the script stops unless override is enabled.
    """
    marker_path = os.path.join(directory, ".script_marker")
    if not os.path.exists(directory):
        logging.info(f"Directory {directory} does not exist. Creating it.")
        os.makedirs(directory)
        return

    # Check if directory contains files
    if os.listdir(directory):
        if os.path.exists(marker_path):
            # Marker file is present, proceed to clear
            logging.info("Marker file found. Clearing the directory.")
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    logging.error(f"Failed to delete {file_path}: {e}")
        else:
            # Marker file is missing
            if not override:
                logging.error(
                    "Directory contains files, but the marker file is missing. Use --override to proceed."
                )
                exit(1)
            else:
                logging.info("Override enabled. Proceeding despite missing marker file.")

def create_marker_file(directory):
    """
    Creates a marker file in the directory to indicate it is managed by this script.
    """
    marker_path = os.path.join(directory, ".script_marker")
    try:
        with open(marker_path, "w") as f:
            f.write("This directory is managed by the immich_downloader script.")
        logging.info(f"Marker file created in {directory}.")
    except Exception as e:
        logging.error(f"Failed to create marker file in {directory}: {e}")

# ------------------------------
# 3. Asset Fetching
# ------------------------------

def fetch_total_assets(endpoint, asset_type, id=None):
    """
    Fetch total number of assets for a person, album, or general pool.
    """
    headers = {"x-api-key": CONFIG["api_key"]}

    try:
        if asset_type == "people":
            url = f"{endpoint}/api/{asset_type}/{id}/statistics"
            response = requests.get(url, headers=headers)
        elif asset_type == "albums":
            url = f"{endpoint}/api/albums/{id}"
            response = requests.get(url, headers=headers)
        else:
            url = f"{endpoint}/api/assets/statistics"
            response = requests.get(url, headers=headers)

        response.raise_for_status()
        data = response.json()

        if asset_type == "people":
            return data.get("assets", 0)
        elif asset_type == "albums":
            return data.get("assetCount", 0)  # Extract the assetCount field
        else:
            return data.get("images", 0)

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching total assets for {asset_type} ID {id}: {e}")
        return 0


def fetch_asset_from_page(endpoint, page, size=1, additional_filters=None):
    url = f"{endpoint}/api/search/metadata"
    headers = {
        "x-api-key": CONFIG["api_key"],
        "Content-Type": "application/json",
    }
    payload = {
        "type": "IMAGE",
        "page": page,
        "size": size,
    }

    if additional_filters:
        payload.update(additional_filters)

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        if "assets" in data and "items" in data["assets"]:
            return data["assets"]["items"]
        else:
            logging.warning(f"No items found on page {page}.")
            return []

    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching asset from page {page}: {e}")
        return []

# ------------------------------
# 4. Image Validation and Processing
# ------------------------------

def validate_image(file_path, config):
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            megapixels = (width * height) / 1_000_000
            aspect_ratio = width / height if height != 0 else 0

            # Check minimum dimensions
            if config.get("min_width") and width < config["min_width"]:
                os.remove(file_path)
                return False
            if config.get("min_height") and height < config["min_height"]:
                os.remove(file_path)
                return False

            # Check minimum megapixels
            if config.get("min_megapixels") and megapixels < config["min_megapixels"]:
                os.remove(file_path)
                return False

            # Check allowed aspect ratios
            if config.get("allowed_aspect_ratios"):
                allowed_ratios = config["allowed_aspect_ratios"]
                if not any(abs(aspect_ratio - ratio) < 0.01 for ratio in allowed_ratios):
                    os.remove(file_path)
                    return False

            # Check screenshot dimensions
            if config.get("screenshot_dimensions"):
                if (width, height) in [tuple(dim) for dim in config["screenshot_dimensions"]]:
                    os.remove(file_path)
                    return False

            # Check if the image is a single color
            pixels = img.getdata()
            unique_colors = set(pixels)
            if len(unique_colors) < 2:  # Single-color images will have only one unique color
                logging.warning(f"Image {file_path} appears to be a single color and will be discarded.")
                os.remove(file_path)
                return False

        return True
    except Exception as e:
        logging.error(f"Error validating image {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False


def process_image(file_path):
    try:
        with Image.open(file_path) as img:
            exif = img._getexif()
            if exif:
                orientation = exif.get(274)
                if orientation == 3:
                    img = img.rotate(180, expand=True)
                elif orientation == 6:
                    img = img.rotate(270, expand=True)
                elif orientation == 8:
                    img = img.rotate(90, expand=True)
            img.save(file_path)
        return True
    except Exception as e:
        logging.warning(f"Error processing image {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False

# ------------------------------
# 5. Downloading and Saving
# ------------------------------

def download_and_validate(asset, output_dir, config):
    file_path = os.path.join(output_dir, f"{asset['id']}.jpg")
    try:
        url = f"{CONFIG['immich_url']}/api/assets/{asset['id']}/original"
        headers = {"x-api-key": CONFIG["api_key"]}
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        if process_image(file_path) and validate_image(file_path, config):
            return file_path
        else:
            return None

    except Exception as e:
        logging.error(f"Error downloading asset {asset['id']}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return None

# ------------------------------
# 6. Main Execution
# ------------------------------

def main():
    global CONFIG
    CONFIG = load_config("config.yaml")
    setup_logging()

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Immich Downloader Script")
    parser.add_argument("--output-dir", type=str, help="Directory to save downloaded images")
    parser.add_argument("--override", action="store_true", help="Override safety checks for the directory")
    args = parser.parse_args()

    # Determine output directory
    output_dir = args.output_dir or CONFIG.get("output_dir")
    if not output_dir:
        logging.error("Output directory not specified.")
        return

    # Directory management
    check_and_clear_directory(output_dir, args.override)
    create_marker_file(output_dir)

    # Configuration parameters
    person_ids = CONFIG.get("person_ids", [])
    album_ids = CONFIG.get("album_ids", [])
    total_images = CONFIG["total_images_to_download"]

    # No specific IDs provided; fetch from general pool
    if not person_ids and not album_ids:
        logging.info("No person IDs or album IDs specified. Fetching from general pool.")
        total_assets = fetch_total_assets(CONFIG["immich_url"], "assets")
        if total_assets == 0:
            logging.warning("No assets found in the general pool.")
            return

        downloaded = download_from_pages(
            CONFIG["immich_url"], total_assets, total_images, output_dir
        )
        logging.info(f"Downloaded {downloaded} images from the general pool.")
    else:
        # Download for each person ID
        for person_id in person_ids:
            logging.info(f"Processing downloads for person ID: {person_id}")
            total_assets = fetch_total_assets(CONFIG["immich_url"], "people", person_id)
            if total_assets == 0:
                logging.warning(f"No assets found for person ID {person_id}.")
                continue

            downloaded = download_from_pages(
                CONFIG["immich_url"], total_assets, total_images, output_dir,
                additional_filters={"personIds": [person_id]}
            )
            logging.info(f"Downloaded {downloaded} images for person ID {person_id}.")

        # Download for each album ID
        for album_id in album_ids:
            logging.info(f"Processing downloads for album ID: {album_id}")
            total_assets = fetch_total_assets(CONFIG["immich_url"], "albums", album_id)
            if total_assets == 0:
                logging.warning(f"No assets found for album ID {album_id}.")
                continue

            downloaded = download_from_pages(
                CONFIG["immich_url"], total_assets, total_images, output_dir,
                additional_filters={"albumIds": [album_id]}
            )
            logging.info(f"Downloaded {downloaded} images for album ID {album_id}.")

def download_from_pages(endpoint, total_assets, total_images, output_dir, additional_filters=None):
    """
    Handles downloading images by fetching assets page by page.
    """
    pages = list(range(1, total_assets + 1))
    random.shuffle(pages)
    downloaded = 0

    for page in pages:
        if downloaded >= total_images:
            break

        assets = fetch_asset_from_page(endpoint, page, size=1, additional_filters=additional_filters)
        for asset in assets:
            if download_and_validate(asset, output_dir, CONFIG):
                downloaded += 1

    return downloaded


if __name__ == "__main__":
    main()


