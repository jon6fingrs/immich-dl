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
import aiohttp
import aiofiles
import asyncio
from asyncio import Semaphore, Lock
from concurrent.futures import ProcessPoolExecutor

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
        config["enable_heic_conversion"] = os.getenv("ENABLE_HEIC_CONVERSION", "true").lower() in ["true", "1"]

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

async def fetch_asset_from_page_async(session, endpoint, page, size=1, additional_filters=None):
    """
    Asynchronously fetch an asset from a specific page using the /search/metadata endpoint.
    """
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
        async with session.post(url, json=payload, headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
            if "assets" in data and "items" in data["assets"]:
                return data["assets"]["items"]
            else:
                logging.warning(f"No items found on page {page}.")
                return []
    except aiohttp.ClientError as e:
        logging.error(f"Error fetching asset from page {page}: {e}")
        return []

async def download_from_pages_async(endpoint, total_assets, total_images, output_dir, additional_filters=None):
    """
    Asynchronously downloads images by fetching assets page by page.
    Reverts to sequential downloading when the remaining images to download is below the concurrency limit.
    """
    pages = list(range(1, total_assets + 1))
    random.shuffle(pages)
    downloaded = 0
    max_parallel_downloads = CONFIG.get("max_parallel_downloads", 5)
    semaphore = Semaphore(max_parallel_downloads)
    lock = Lock()  # Ensure thread-safe updates to the downloaded counter

    async def process_page(session, page):
        nonlocal downloaded
        async with semaphore:  # Limit the number of concurrent tasks
            assets = await fetch_asset_from_page_async(session, endpoint, page, size=1, additional_filters=additional_filters)
            for asset in assets:
                async with lock:  # Ensure only one task updates the count at a time
                    if downloaded >= total_images:
                        return
                if await download_and_validate_async(asset, output_dir, CONFIG):
                    async with lock:  # Lock again for incrementing
                        downloaded += 1

    async with aiohttp.ClientSession() as session:
        while downloaded < total_images and pages:
            if total_images - downloaded <= max_parallel_downloads:
                # Sequential processing for remaining images
                for page in pages[:total_images - downloaded]:
                    assets = await fetch_asset_from_page_async(session, endpoint, page, size=1, additional_filters=additional_filters)
                    for asset in assets:
                        async with lock:
                            if downloaded >= total_images:
                                return downloaded
                        if await download_and_validate_async(asset, output_dir, CONFIG):
                            async with lock:
                                downloaded += 1
                    pages.pop(0)  # Remove processed page
            else:
                # Concurrent processing
                tasks = [process_page(session, page) for page in pages[:max_parallel_downloads]]
                await asyncio.gather(*tasks)
                pages = pages[max_parallel_downloads:]  # Remove processed pages

    return downloaded

# ------------------------------
# 4. Image Validation and Processing
# ------------------------------


def process_and_validate_image(file_path, config):
    try:
        # Check if the file is a HEIC image
        if file_path.lower().endswith(".heic") and config.get("enable_heic_conversion", True):
            jpg_path = file_path.rsplit(".", 1)[0] + ".jpg"
            if not convert_heic_to_jpg(file_path, jpg_path):
                return False
            file_path = jpg_path  # Update file path to the new JPEG file
        with Image.open(file_path) as img:
            width, height = img.size
            megapixels = (width * height) / 1_000_000
            aspect_ratio = width / height if height != 0 else 0

            # Check conditions
            if config.get("min_width") and width < config["min_width"]:
                os.remove(file_path)
                return False
            if config.get("min_height") and height < config["min_height"]:
                os.remove(file_path)
                return False
            if config.get("min_megapixels") and megapixels < config["min_megapixels"]:
                os.remove(file_path)
                return False

            # Rotate based on EXIF orientation
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
        logging.error(f"Error processing image {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False

def convert_heic_to_jpg(heic_path, jpg_path):
    """
    Converts a HEIC file to a JPEG file using an external converter like `heif-convert`.
    """
    try:
        result = os.system(f"heif-convert {heic_path} {jpg_path}")
        if result == 0:
            os.remove(heic_path)  # Remove the original HEIC file if conversion succeeds
            return True
        else:
            logging.error(f"HEIC to JPEG conversion failed for {heic_path}")
            return False
    except Exception as e:
        logging.error(f"Error during HEIC to JPEG conversion for {heic_path}: {e}")
        return False


# ------------------------------
# 5. Downloading and Saving
# ------------------------------

async def download_and_validate_async(asset, output_dir, config):
    file_path = os.path.join(output_dir, f"{asset['id']}.jpg")
    url = f"{config['immich_url']}/api/assets/{asset['id']}/original"
    headers = {"x-api-key": config["api_key"]}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)

        # Offload image processing to a separate thread
        loop = asyncio.get_running_loop()
        success = await loop.run_in_executor(None, process_and_validate_image, file_path, config)
        if success:
            return file_path
        else:
            return None
    except Exception as e:
        logging.error(f"Error downloading or validating asset {asset['id']}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return None

# ------------------------------
# 6. Main Execution
# ------------------------------



async def main_async():
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
    total_images_per_id = CONFIG["total_images_to_download"]  # Apply per ID

    # No specific IDs provided; fetch from general pool
    if not person_ids and not album_ids:
        logging.info("No person IDs or album IDs specified. Fetching from general pool.")
        total_assets = fetch_total_assets(CONFIG["immich_url"], "assets")
        if total_assets == 0:
            logging.warning("No assets found in the general pool.")
            return

        downloaded = await download_from_pages_async(
            CONFIG["immich_url"], total_assets, total_images_per_id, output_dir
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

            downloaded = await download_from_pages_async(
                CONFIG["immich_url"], total_assets, total_images_per_id, output_dir,
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

            downloaded = await download_from_pages_async(
                CONFIG["immich_url"], total_assets, total_images_per_id, output_dir,
                additional_filters={"albumIds": [album_id]}
            )
            logging.info(f"Downloaded {downloaded} images for album ID {album_id}.")















if __name__ == "__main__":
    asyncio.run(main_async())

