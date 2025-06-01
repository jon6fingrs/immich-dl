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
import subprocess
from pillow_heif import register_heif_opener
import glob
import piexif
from datetime import datetime
import concurrent.futures  # <-- Added for concurrent validation

register_heif_opener()

# Global references
CONFIG = {}
supports_atomic_write = False
VALIDATION_EXECUTOR = None  # We'll assign a real thread pool in main_async
HEIF_CONVERT_SUPPORTS_OUTPUT_OPTION = False
ORIENTATION_MAP = {
    "Rotate 90 CW": 270,
    "Rotate 180": 180,
    "Rotate 270 CW": 90,
    "Normal": 0,
}

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
        config["screenshot_dimensions"] = json.loads(
            os.getenv("SCREENSHOT_DIMENSIONS", json.dumps(config.get("screenshot_dimensions", [])))
        )
        
        min_megapixels = os.getenv("MIN_MEGAPIXELS") or config.get("min_megapixels")
        config["min_megapixels"] = float(min_megapixels) if min_megapixels is not None else None

        min_width = os.getenv("MIN_WIDTH") or config.get("min_width")
        config["min_width"] = int(min_width) if min_width is not None else None

        min_height = os.getenv("MIN_HEIGHT") or config.get("min_height")
        config["min_height"] = int(min_height) if min_height is not None else None
        
        config["override"] = os.getenv("OVERRIDE", str(config.get("override", False))).lower() in ["true", "1"]
        config["max_parallel_downloads"] = int(
            os.getenv("MAX_PARALLEL_DOWNLOADS", config.get("max_parallel_downloads", 5))
        )
        config["dry_run"] = os.getenv("DRY_RUN", str(config.get("dry_run", False))).lower() in ["true", "1"]
        config["enable_heic_conversion"] = os.getenv(
            "ENABLE_HEIC_CONVERSION", str(config.get("enable_heic_conversion", True))
        ).lower() in ["true", "1"]

        # Parse min_date and max_date
        min_date_str = os.getenv("MIN_DATE", config.get("min_date"))
        max_date_str = os.getenv("MAX_DATE", config.get("max_date"))

        config["min_date"] = (
            datetime.strptime(min_date_str, "%Y-%m-%d") if min_date_str else None
        )
        config["max_date"] = (
            datetime.strptime(max_date_str, "%Y-%m-%d") if max_date_str else None
        )

        config["max_validation_workers"] = int(
            os.getenv("MAX_VALIDATION_WORKERS", config.get("max_validation_workers", 4))
        )
        config["max_heic_conversion_workers"] = int(
            os.getenv("MAX_HEIC_CONVERSION_WORKERS", config.get("max_heic_conversion_workers", 4))
        )
        
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

def check_heif_convert_support():
    """
    Checks if 'heif-convert' is installed and supports the '-o' or '--output' option.
    Updates the global variable accordingly.
    """
    global HEIF_CONVERT_SUPPORTS_OUTPUT_OPTION

    # Check if 'heif-convert' is installed
    heif_convert_path = shutil.which('heif-convert')
    if heif_convert_path is None:
        logging.error("'heif-convert' command not found. Disabling HEIC conversion.")
        return False

    try:
        # Execute 'heif-convert --help' and capture its output
        result = subprocess.run(
            ['heif-convert', '--help'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            help_output = result.stdout
            # Check if '-o' or '--output' is mentioned in the help output
            if '-o, --output' in help_output:
                HEIF_CONVERT_SUPPORTS_OUTPUT_OPTION = True
            else:
                HEIF_CONVERT_SUPPORTS_OUTPUT_OPTION = False
            return True
        else:
            logging.error("Failed to execute 'heif-convert --help'.")
            return False
    except Exception as e:
        logging.error(f"An error occurred while checking 'heif-convert' support: {e}")
        return False

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
            assets = await fetch_asset_from_page_async(
                session, endpoint, page, size=1, additional_filters=additional_filters
            )
            for asset in assets:
                async with lock:
                    if downloaded >= total_images:
                        return
                # Download & validate concurrently
                file_path = await download_and_validate_async(asset, output_dir, CONFIG)
                if file_path:
                    async with lock:
                        downloaded += 1

    async with aiohttp.ClientSession() as session:
        while downloaded < total_images and pages:
            # If what's left to download is less than concurrency, do it sequentially
            if total_images - downloaded <= max_parallel_downloads:
                for page in pages[:total_images - downloaded]:
                    assets = await fetch_asset_from_page_async(
                        session, endpoint, page, size=1, additional_filters=additional_filters
                    )
                    for asset in assets:
                        async with lock:
                            if downloaded >= total_images:
                                return downloaded
                        file_path = await download_and_validate_async(asset, output_dir, CONFIG)
                        if file_path:
                            async with lock:
                                downloaded += 1
                    pages.pop(0)  # Remove processed page
            else:
                # Otherwise run tasks concurrently
                tasks = [process_page(session, page) for page in pages[:max_parallel_downloads]]
                await asyncio.gather(*tasks)
                pages = pages[max_parallel_downloads:]  # Remove processed pages

    return downloaded
    
# ------------------------------
# 4. Image Validation and Processing
# ------------------------------

def process_and_validate_image(file_path, config):
    """
    Process and validate an image file. Validates date if required, checks dimensions.
    """
    try:
        logging.info(f"Processing file {file_path}.")
        file_ext = file_path.lower().split('.')[-1]

        # Step 1: Extract EXIF if required
        exif = {}
        if config.get("min_date") or config.get("max_date") or config.get("screenshot_dimensions"):
            exif = _extract_exif_with_exiftool(file_path)

        # Step 2: Validate date if needed
        if config.get("min_date") or config.get("max_date"):
            date_taken = _extract_date_from_exif(exif)
            if not _validate_date(date_taken, file_path, config):
                return False

        # Step 3: Open image and validate dimensions if needed
        if (
            config.get("min_megapixels") or
            config.get("min_width") or
            config.get("min_height") or
            config.get("screenshot_dimensions")
        ):
            with Image.open(file_path) as img:
                width, height = img.size
                megapixels = (width * height) / 1_000_000

                # Validate dimensions and megapixels
                if not _validate_dimensions(img, file_path, config, width, height, megapixels, exif):
                    return False

        logging.info(f"Image {file_path} passed all validation checks.")
        return True

    except Exception as e:
        logging.error(f"Error processing image {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return False

def _extract_date_from_exif(exif):
    """
    Extract the date the picture was taken from EXIF data.
    Returns a datetime object or None.
    """
    try:
        # Attempt to find the date in common EXIF date fields
        date_str = (
            exif.get("DateTimeOriginal") or
            exif.get("CreateDate") or
            exif.get("ModifyDate") or
            exif.get("exif:DateTimeOriginal") or
            exif.get("exif:DateTime")
        )
        if date_str:
            return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
    except Exception as e:
        logging.warning(f"Failed to parse date from EXIF data: {e}")
    return None

def _extract_exif_with_exiftool(file_path):
    """
    Extract EXIF data from any image using ExifTool.
    Returns a dictionary of EXIF fields.
    """
    try:
        result = subprocess.run(
            ["exiftool", "-json", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        exif_data = json.loads(result.stdout)
        return exif_data[0] if exif_data else {}
    except Exception as e:
        logging.error(f"Error extracting EXIF data from {file_path}: {e}")
        return {}

def _validate_date(date_taken, file_path, config):
    """
    Validate the date against min_date and max_date.
    """
    if not date_taken:
        logging.warning(f"No valid date found for {file_path}, and date filtering is enabled. Discarding.")
        os.remove(file_path)
        return False

    min_date = config.get("min_date")
    max_date = config.get("max_date")

    if min_date and date_taken < min_date:
        logging.warning(f"Image {file_path} taken on {date_taken} is before the minimum date. Discarding.")
        os.remove(file_path)
        return False

    if max_date and date_taken > max_date:
        logging.warning(f"Image {file_path} taken on {date_taken} is after the maximum date. Discarding.")
        os.remove(file_path)
        return False

    return True

def _validate_dimensions(img, file_path, config, width, height, megapixels, exif):
    """
    Validate dimensions, megapixels, and screenshot checks.
    """
    # Screenshot dimensions check
    if config.get("screenshot_dimensions"):
        if (width, height) in [tuple(dim) for dim in config["screenshot_dimensions"]]:
            if not exif or not exif.get("Make"):  # Fail if EXIF or 'Make' field is missing
                logging.warning(f"Image {file_path} matches screenshot dimensions but lacks EXIF or camera 'Make' field. Discarding.")
                os.remove(file_path)
                return False

    # Minimum megapixels
    if config.get("min_megapixels") and megapixels < config["min_megapixels"]:
        logging.warning(f"Image {file_path} below minimum megapixels. Discarding.")
        os.remove(file_path)
        return False

    # Minimum width and height
    if config.get("min_width") and width < config["min_width"]:
        logging.warning(f"Image {file_path} below minimum width. Discarding.")
        os.remove(file_path)
        return False

    if config.get("min_height") and height < config["min_height"]:
        logging.warning(f"Image {file_path} below minimum height. Discarding.")
        os.remove(file_path)
        return False

    return True

async def convert_heic_files_concurrently(
    output_dir, 
    max_workers: int
):
    """
    Converts all HEIC files in the output directory to JPEG using heif-convert,
    in parallel using a thread pool.
    """
    heic_files = glob.glob(os.path.join(output_dir, "*.heic"))
    if not heic_files:
        logging.info("No HEIC files found for conversion.")
        return

    loop = asyncio.get_running_loop()
    def convert_single_heic(heic_path):
        """
        Runs heif-convert on a single file, then removes the .heic if successful.
        """
        jpg_path = os.path.splitext(heic_path)[0] + ".jpg"
        
        
        if HEIF_CONVERT_SUPPORTS_OUTPUT_OPTION:
            cmd = (
                f'heif-convert -v "{heic_path}" || '
                f'(mv "{heic_path}" "{jpg_path}")'
            )
        else:
            cmd = f'heif-convert "{heic_path}" "{jpg_path}"'
        
        try:
            result = os.system(cmd)
            if not os.path.exists(jpg_path):
                os.rename(heic_path, jpg_path)

            if result == 0:
                subprocess.run(
                    ["exiftool", "-overwrite_original_in_place", "-Orientation=", f"{jpg_path}"],
                    check=True
                )
                os.remove(heic_path)
                logging.info(f"Converted {heic_path} to {jpg_path}")
            else:
                logging.error(f"HEIC to JPEG conversion failed for {heic_path}")
        except Exception as e:
            logging.error(f"Error during HEIC to JPEG conversion for {heic_path}: {e}")

    # Create a thread pool just for this conversion
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        tasks = []
        for heic_file in heic_files:
            # Schedule each conversion in the thread pool
            tasks.append(loop.run_in_executor(executor, convert_single_heic, heic_file))

        # Wait for all tasks to complete
        await asyncio.gather(*tasks)

    logging.info("Concurrent HEIC conversion complete.")


# ------------------------------
# 5. Downloading and Saving
# ------------------------------

async def download_and_validate_async(asset, output_dir, config):
    """
    Downloads an asset from Immich, then offloads the CPU-bound validation to a thread pool.
    Returns the file path if successful, None otherwise.
    """
    original_filename = asset.get("originalFileName", "default.jpg")
    _, file_extension = os.path.splitext(original_filename)

    file_extension = file_extension.lower()
    if not file_extension.startswith("."):
        file_extension = f".{file_extension}"

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
                        fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        with os.fdopen(fd, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                    except FileExistsError:
                        logging.info(f"File {file_path} already exists. Skipping.")
                        return None
                else:
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

        # **Offload** validation to our shared thread pool:
        loop = asyncio.get_running_loop()
        # Use the global VALIDATION_EXECUTOR for concurrency
        success = await loop.run_in_executor(
            VALIDATION_EXECUTOR,
            process_and_validate_image,
            file_path,
            config
        )
        return file_path if success else None

    except Exception as e:
        logging.error(f"Error downloading or validating asset {asset['id']}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return None

# ------------------------------
# 6. Main Execution
# ------------------------------

def get_filesystem_type(directory):
    """
    Determine the filesystem type of the given directory.
    """
    try:
        result = subprocess.run(["df", "-T", directory], stdout=subprocess.PIPE, text=True, check=True)
        lines = result.stdout.splitlines()
        if len(lines) > 1:
            return lines[1].split()[1]
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

    output_dir = args.output_dir or CONFIG.get("output_dir")
    if not output_dir:
        logging.error("Output directory not specified.")
        return

    global supports_atomic_write
    filesystem_type = get_filesystem_type(output_dir)
    supports_atomic_write = filesystem_type in ["nfs", "ntfs", "ext4", "xfs"]
    logging.info(f"Filesystem type: {filesystem_type} - Supports atomic write: {supports_atomic_write}")

    check_and_prepare_directory(output_dir, args.override)

    # -------------
    # NEW: Create a thread pool for concurrent CPU-bound validations
    global VALIDATION_EXECUTOR
    VALIDATION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
        max_workers=CONFIG["max_validation_workers"]
    )
    # Tweak max_workers for your system’s CPU
    # -------------

    # Configuration parameters
    person_ids = CONFIG.get("person_ids", [])
    album_ids = CONFIG.get("album_ids", [])
    total_images_per_id = CONFIG["total_images_to_download"]

    # If no IDs given, download from general pool
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
        # Person IDs
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

        # Album IDs
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

    # Perform HEIC -> JPEG conversion if enabled
 #   if CONFIG.get("enable_heic_conversion", True):
 #       logging.info("Starting HEIC to JPEG conversion for downloaded images.")
 #       convert_heic_files(output_dir)

    if CONFIG.get("enable_heic_conversion", True):
        heif_convert_available = check_heif_convert_support()
        if not heif_convert_available:
            CONFIG["enable_heic_conversion"] = False

    if CONFIG.get("enable_heic_conversion", True):
        logging.info("Starting concurrent HEIC to JPEG conversion for downloaded images.")
        await convert_heic_files_concurrently(CONFIG["output_dir"], max_workers=CONFIG["max_heic_conversion_workers"])

    # Cleanup the thread pool
    VALIDATION_EXECUTOR.shutdown(wait=True)
    logging.info("Validation thread pool shut down.")

if __name__ == "__main__":
    asyncio.run(main_async())
