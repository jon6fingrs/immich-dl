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
import subprocess
from pillow_heif import register_heif_opener
import glob
import piexif

register_heif_opener()

supports_atomic_write = False

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
        config["min_megapixels"] = float(os.getenv("MIN_MEGAPIXELS")) if "MIN_MEGAPIXELS" in os.environ else config.get("min_megapixels")
        config["screenshot_dimensions"] = json.loads(
            os.getenv("SCREENSHOT_DIMENSIONS", json.dumps(config.get("screenshot_dimensions", [])))
        )
        config["min_width"] = int(os.getenv("MIN_WIDTH")) if "MIN_WIDTH" in os.environ else config.get("min_width")
        config["min_height"] = int(os.getenv("MIN_HEIGHT")) if "MIN_HEIGHT" in os.environ else config.get("min_height")
        config["override"] = os.getenv("OVERRIDE", str(config.get("override", False))).lower() in ["true", "1"]
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

def check_and_prepare_directory(directory, override):
    """
    Ensures the directory is prepared for use:
    - If the directory doesn't exist, it is created.
    - If the directory exists and has files:
        - If the marker file is present, the directory is cleared.
        - If the marker file is absent and override is enabled, the directory is cleared.
        - If the marker file is absent and override is not enabled, the script stops.
    - Creates a marker file to indicate the directory is managed by this script.
    """
    marker_path = os.path.join(directory, ".script_marker")
    
    # Ensure the directory exists
    if not os.path.exists(directory):
        logging.info(f"Directory {directory} does not exist. Creating it.")
        os.makedirs(directory)
    elif os.listdir(directory):  # Directory exists and contains files
        if os.path.exists(marker_path):
            logging.info("Marker file found. Clearing the directory.")
        elif override:
            logging.info("Marker file absent, but override enabled. Clearing the directory.")
        else:
            logging.error(
                "Directory contains files, but the marker file is missing. Use --override to proceed."
            )
            exit(1)
        
        # Clear the directory
        for filename in os.listdir(directory):
            file_path = os.path.join(directory, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                logging.error(f"Failed to delete {file_path}: {e}")

    # Create the marker file
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
        with Image.open(file_path) as img:
            logging.info(f"Processing file {file_path} with format {img.format}.")

            # Get initial dimensions and megapixels before rotation
            width, height = img.size
            megapixels = (width * height) / 1_000_000

            # Attempt to retrieve EXIF data if supported
            exif = None
            if hasattr(img, "_getexif"):
                try:
                    exif = img._getexif()
                except Exception as e:
                    logging.warning(f"Failed to retrieve EXIF for {file_path}: {e}")

            # Check if the image matches screenshot dimensions and lacks camera EXIF data
            if config.get("screenshot_dimensions"):
                if (width, height) in [tuple(dim) for dim in config["screenshot_dimensions"]]:
                    if not exif or not exif.get(271):  # Check for "Make" field in EXIF data
                        logging.warning(f"Image {file_path} matches screenshot dimensions and lacks camera EXIF data. Discarding.")
                        os.remove(file_path)
                        return False

            # Check minimum megapixels
            if config.get("min_megapixels") and megapixels < config["min_megapixels"]:
                logging.warning(f"Image {file_path} below minimum megapixels. Discarding.")
                os.remove(file_path)
                return False

            exif_binary = img.info.get("exif") if "exif" in img.info else None
            exif_data = piexif.load(exif_binary)
            # Rotate based on EXIF orientation
            if exif:
                orientation = exif.get(274)
                if orientation == 3:
                    img = img.rotate(180, expand=True)
                    exif_data["0th"][piexif.ImageIFD.Orientation] = 1
                    exif_binary = piexif.dump(exif_data)
                    logging.info(f"Orientation corrected for {file_path}.")
                elif orientation == 6:
                    img = img.rotate(270, expand=True)
                    exif_data["0th"][piexif.ImageIFD.Orientation] = 1
                    exif_binary = piexif.dump(exif_data)
                    logging.info(f"Orientation corrected for {file_path}.")
                elif orientation == 8:
                    img = img.rotate(90, expand=True)
                    exif_data["0th"][piexif.ImageIFD.Orientation] = 1
                    exif_binary = piexif.dump(exif_data)
                    logging.info(f"Orientation corrected for {file_path}.")
                    
            # Save the image with its original or updated format

            if exif_binary:
                # Save as JPEG with EXIF metadata
                img.save(file_path, img.format, exif=exif_binary)
            else:
                # Save in the original format without EXIF metadata
                img.save(file_path, img.format)

            # Apply minimum width and height checks
            width, height = img.size
            if config.get("min_width") and width < config["min_width"]:
                logging.warning(f"Image {file_path} below minimum width. Discarding.")
                os.remove(file_path)
                return False
            if config.get("min_height") and height < config["min_height"]:
                logging.warning(f"Image {file_path} below minimum height. Discarding.")
                os.remove(file_path)
                return False

        return True
    except Exception as e:
        logging.error(f"Error processing image {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False



def convert_heic_files(output_dir):
    """
    Converts all HEIC files in the output directory to JPEG using heic-convert.
    """
    heic_files = glob.glob(os.path.join(output_dir, "*.heic"))
    for heic_path in heic_files:
        jpg_path = heic_path.rsplit(".", 1)[0] 
        try:
            # Use heic-convert to convert the file
            result = os.system(f"heif-convert -v -o {jpg_path} {heic_path}")
            if result == 0:
                os.remove(heic_path)  # Remove the original HEIC file if conversion succeeds
                logging.info(f"Converted {heic_path} to {jpg_path}.jpg")
            else:
                logging.error(f"HEIC to JPEG conversion failed for {heic_path}.jpg")
        except Exception as e:
            logging.error(f"Error during HEIC to JPEG conversion for {heic_path}: {e}")


# ------------------------------
# 5. Downloading and Saving
# ------------------------------

async def download_and_validate_async(asset, output_dir, config):
    # Extract the original file extension from the filename
    original_filename = asset.get("originalFileName", "default.jpg")  # Default to avoid errors
    _, file_extension = os.path.splitext(original_filename)  # Extract the extension

    # Ensure the file extension is lowercased for consistency
    file_extension = file_extension.lower()
    if not file_extension.startswith("."):
        file_extension = f".{file_extension}"

    # Construct the file path with the original extension
    file_path = os.path.join(output_dir, f"{asset['id']}{file_extension}")
    url = f"{config['immich_url']}/api/assets/{asset['id']}/original"
    headers = {"x-api-key": config["api_key"]}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                response.raise_for_status()
                
                global supports_atomic_write
                
                if supports_atomic_write:
                    try:
                        # Attempt atomic write
                        fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        with os.fdopen(fd, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                    except FileExistsError:
                        # File already exists, skip
                        logging.info(f"File {file_path} already exists. Skipping.")
                        return None
                else:
                    # Fallback to standard writing
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

# Function to determine filesystem type
def get_filesystem_type(directory):
    """
    Determine the filesystem type of the given directory.
    """
    try:
        # Use 'df -T' to find the filesystem type
        result = subprocess.run(["df", "-T", directory], stdout=subprocess.PIPE, text=True, check=True)
        lines = result.stdout.splitlines()
        # Extract filesystem type from output
        if len(lines) > 1:
            return lines[1].split()[1]  # Filesystem type is in the second column
    except Exception as e:
        logging.warning(f"Failed to determine filesystem type: {e}")
    return None

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

    # Determine filesystem type once
    global supports_atomic_write  # Declare it as global to modify it here
    filesystem_type = get_filesystem_type(output_dir)
    supports_atomic_write = filesystem_type in ["nfs", "ntfs", "ext4", "xfs"]
    logging.info(f"Filesystem type: {filesystem_type} - Supports atomic write: {supports_atomic_write}")


    # Directory management
    check_and_prepare_directory(output_dir, args.override)

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

    if CONFIG.get("enable_heic_conversion", True):
        logging.info("Starting HEIC to JPEG conversion for downloaded images.")
        convert_heic_files(CONFIG["output_dir"])

if __name__ == "__main__":
    asyncio.run(main_async())
