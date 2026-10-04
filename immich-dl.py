#!/usr/bin/env python3
"""
Immich Downloader

Pulls a random selection of images from an Immich server into a local folder
(e.g. for the Kodi Picture Slideshow screensaver). Each run replaces the
previous selection; the swap only happens once the new set has been
downloaded, so the folder is never left empty while a run is in progress or
when the server is unreachable.
"""

import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import aiohttp
import yaml
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

MARKER_NAME = ".script_marker"
STAGING_NAME = ".immich-dl-staging"

# Formats Kodi displays natively; anything else is converted to JPEG.
KODI_FORMATS = {"JPEG", "PNG", "GIF"}
# Extensions Pillow (+ pillow-heif) can decode. Originals with any other
# extension (RAW files such as DNG/CR2/NEF) are fetched as Immich's
# full-size JPEG rendition instead.
PILLOW_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".hif", ".avif",
    ".webp", ".tif", ".tiff", ".bmp",
}
# EXIF orientations that swap width and height.
ROTATED_ORIENTATIONS = {"5", "6", "7", "8"}
EXIF_ORIENTATION_TAG = 0x0112
EXIF_MAKE_TAG = 0x010F

# Immich server versions that changed the API this script relies on.
EDITED_PARAM_VERSION = (2, 5, 0)  # ?edited=true on /assets/{id}/original
FILTER_API_VERSION = (3, 2, 0)  # "filter" object on /search/random

SEARCH_BATCH_MAX = 1000  # Immich caps /search/random at 1000 results
MAX_EMPTY_ROUNDS = 3  # stop asking for more once the pool is exhausted


# ------------------------------
# 1. Configuration and Logging
# ------------------------------

def setup_logging(log_file):
    """Log to the console and to a rotating log file."""
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file:
        try:
            file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:
            logging.warning(f"Cannot write log file {log_file}: {e}")


def _setting(yaml_config, key, default=None):
    """Environment variable (upper-case key) wins over YAML, which wins over the default."""
    env_value = os.getenv(key.upper())
    if env_value is not None and env_value != "":
        return env_value
    value = yaml_config.get(key)
    return default if value is None else value


def _as_bool(value):
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _as_list(value):
    if isinstance(value, str):
        return json.loads(value)
    return list(value or [])


def _as_optional(value, cast):
    return cast(value) if value not in (None, "") else None


def _as_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d")


def load_config(config_file):
    try:
        with open(config_file, "r") as f:
            yaml_config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logging.info(f"Configuration file {config_file} not found. Using environment variables only.")
        yaml_config = {}

    config = {
        "immich_url": str(_setting(yaml_config, "immich_url", "")).rstrip("/"),
        "api_key": str(_setting(yaml_config, "api_key", "")),
        "output_dir": str(_setting(yaml_config, "output_dir", "/downloads")),
        "total_images_to_download": int(_setting(yaml_config, "total_images_to_download", 10)),
        "person_ids": _as_list(_setting(yaml_config, "person_ids", [])),
        "album_ids": _as_list(_setting(yaml_config, "album_ids", [])),
        "screenshot_dimensions": [tuple(d) for d in _as_list(_setting(yaml_config, "screenshot_dimensions", []))],
        "min_megapixels": _as_optional(_setting(yaml_config, "min_megapixels"), float),
        "min_width": _as_optional(_setting(yaml_config, "min_width"), int),
        "min_height": _as_optional(_setting(yaml_config, "min_height"), int),
        "min_date": _as_date(_setting(yaml_config, "min_date")),
        "max_date": _as_date(_setting(yaml_config, "max_date")),
        "include_archived": _as_bool(_setting(yaml_config, "include_archived", False)),
        "use_edited": _as_bool(_setting(yaml_config, "use_edited", True)),
        "override": _as_bool(_setting(yaml_config, "override", False)),
        "dry_run": _as_bool(_setting(yaml_config, "dry_run", False)),
        "enable_heic_conversion": _as_bool(_setting(yaml_config, "enable_heic_conversion", True)),
        "write_location_caption": _as_bool(_setting(yaml_config, "write_location_caption", False)),
        "caption_omit_countries": _as_list(
            _setting(yaml_config, "caption_omit_countries", ["United States of America", "United States"])
        ),
        "max_parallel_downloads": int(_setting(yaml_config, "max_parallel_downloads", 5)),
        "max_validation_workers": int(_setting(yaml_config, "max_validation_workers", 4)),
        "request_timeout": int(_setting(yaml_config, "request_timeout", 300)),
    }
    # Deprecated option, kept so existing configs keep working.
    if _setting(yaml_config, "max_heic_conversion_workers") is not None:
        logging.info("max_heic_conversion_workers is deprecated; conversion now uses max_validation_workers.")

    if not config["immich_url"] or not config["api_key"]:
        raise ValueError("Both IMMICH_URL and API_KEY must be specified, either in the YAML file or as environment variables.")
    if config["total_images_to_download"] < 1:
        raise ValueError("TOTAL_IMAGES_TO_DOWNLOAD must be at least 1.")
    if config["min_date"] and config["max_date"] and config["min_date"] > config["max_date"]:
        raise ValueError("MIN_DATE must not be after MAX_DATE.")

    redacted = {**config, "api_key": "***"}
    logging.info(f"Configuration loaded: {redacted}")
    return config


# ------------------------------
# 2. Directory Management
# ------------------------------

def check_directory(directory, override):
    """
    Make sure the output directory is safe to manage. A directory that already
    contains files but no marker file is refused unless override is enabled,
    so the script never wipes a folder it does not own.
    """
    if not os.path.exists(directory):
        logging.info(f"Directory {directory} does not exist. Creating it.")
        os.makedirs(directory)
        return

    entries = [e for e in os.listdir(directory) if e != STAGING_NAME]
    if entries and MARKER_NAME not in entries:
        if not override:
            logging.error("Directory contains files, but the marker file is missing. Use --override (or OVERRIDE=true) to proceed.")
            sys.exit(1)
        logging.info("Marker file absent, but override enabled. Directory will be replaced.")


def _remove_path(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as e:
        logging.error(f"Failed to delete {path}: {e}")


def prepare_staging(directory):
    staging = os.path.join(directory, STAGING_NAME)
    if os.path.exists(staging):
        _remove_path(staging)
    os.makedirs(staging)
    return staging


def swap_in_staging(directory, staging):
    """Replace the previous selection with the newly downloaded one."""
    for entry in os.listdir(directory):
        if entry not in (STAGING_NAME, MARKER_NAME):
            _remove_path(os.path.join(directory, entry))
    for entry in os.listdir(staging):
        os.replace(os.path.join(staging, entry), os.path.join(directory, entry))
    os.rmdir(staging)

    with open(os.path.join(directory, MARKER_NAME), "w") as f:
        f.write("This directory is managed by the immich_downloader script.")


# ------------------------------
# 3. Immich API
# ------------------------------

class ImmichClient:
    def __init__(self, session, config):
        self.session = session
        self.base_url = config["immich_url"]
        self.headers = {"x-api-key": config["api_key"], "Accept": "application/json"}
        self.version = (0, 0, 0)

    async def fetch_version(self):
        async with self.session.get(f"{self.base_url}/api/server/version", headers=self.headers) as response:
            response.raise_for_status()
            data = await response.json()
        self.version = (data.get("major", 0), data.get("minor", 0), data.get("patch", 0))
        return self.version

    def build_search(self, config, person_id=None, album_id=None):
        """Build a /search/random body for the server's API generation."""
        min_date, max_date = config["min_date"], config["max_date"]
        # max_date includes the whole day.
        before = max_date + timedelta(days=1) if max_date else None

        if self.version >= FILTER_API_VERSION:
            search_filter = {"type": {"eq": "IMAGE"}}
            visibility = ["timeline", "archive"] if config["include_archived"] else ["timeline"]
            search_filter["visibility"] = {"in": visibility}
            if person_id:
                search_filter["personIds"] = {"any": [person_id]}
            if album_id:
                search_filter["albumIds"] = {"any": [album_id]}
            taken_at = {}
            if min_date:
                taken_at["gte"] = _iso(min_date)
            if before:
                taken_at["lt"] = _iso(before)
            if taken_at:
                search_filter["takenAt"] = taken_at
            return {"filter": search_filter, "withExif": True}

        # Immich < 3.2: flat search fields.
        body = {"type": "IMAGE", "withExif": True}
        if not config["include_archived"]:
            body["visibility"] = "timeline"
        if person_id:
            body["personIds"] = [person_id]
        if album_id:
            body["albumIds"] = [album_id]
        if min_date:
            body["takenAfter"] = _iso(min_date)
        if before:
            body["takenBefore"] = _iso(before)
        return body

    async def random_assets(self, search, size):
        body = {**search, "size": max(1, min(size, SEARCH_BATCH_MAX))}
        async with self.session.post(f"{self.base_url}/api/search/random", json=body, headers=self.headers) as response:
            if response.status >= 400:
                detail = await response.text()
                raise aiohttp.ClientResponseError(
                    response.request_info, response.history, status=response.status,
                    message=f"{response.reason}: {detail[:300]}",
                )
            return await response.json()

    def download_url(self, asset, use_edited):
        _, ext = os.path.splitext(asset.get("originalFileName") or "")
        if ext.lower() in PILLOW_EXTENSIONS:
            url = f"{self.base_url}/api/assets/{asset['id']}/original"
            params = {}
        else:
            # RAW and other formats Pillow cannot read: use Immich's JPEG rendition.
            url = f"{self.base_url}/api/assets/{asset['id']}/thumbnail"
            params = {"size": "fullsize"}
        if use_edited and self.version >= EDITED_PARAM_VERSION:
            params["edited"] = "true"
        return url, params

    async def download(self, url, params, file_path):
        tmp_path = file_path + ".part"
        try:
            async with self.session.get(url, params=params, headers={"x-api-key": self.headers["x-api-key"]}) as response:
                response.raise_for_status()
                with open(tmp_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        f.write(chunk)
            os.replace(tmp_path, file_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ------------------------------
# 4. Image Validation and Processing
# ------------------------------

def check_dimensions(width, height, make, config):
    """Return the reason an image should be skipped, or None if it is acceptable."""
    if not width or not height:
        return None
    if (width, height) in config["screenshot_dimensions"] and not make:
        return f"matches screenshot dimensions {width}x{height} and has no camera make"
    if config["min_megapixels"] and (width * height) / 1_000_000 < config["min_megapixels"]:
        return f"below {config['min_megapixels']} megapixels ({width}x{height})"
    if config["min_width"] and width < config["min_width"]:
        return f"narrower than {config['min_width']}px ({width}x{height})"
    if config["min_height"] and height < config["min_height"]:
        return f"shorter than {config['min_height']}px ({width}x{height})"
    return None


def prefilter_asset(asset, config):
    """Skip assets using Immich's metadata so they are never downloaded."""
    exif = asset.get("exifInfo") or {}
    width, height = exif.get("exifImageWidth"), exif.get("exifImageHeight")
    if str(exif.get("orientation") or "") in ROTATED_ORIENTATIONS:
        width, height = height, width
    return check_dimensions(width, height, exif.get("make"), config)


def location_caption(asset, config):
    """Format Immich's reverse-geocoded location, e.g. "Boston, Massachusetts"."""
    exif = asset.get("exifInfo") or {}
    parts = [exif.get("city"), exif.get("state")]
    country = exif.get("country")
    if country and country not in config["caption_omit_countries"]:
        parts.append(country)
    parts = [p for i, p in enumerate(parts) if p and p not in parts[:i]]
    return ", ".join(parts) or None


def process_image(file_path, asset, config):
    """
    Validate the downloaded file, convert it to JPEG if Kodi cannot display it,
    and optionally write the location caption. Returns the final path or None.
    """
    try:
        with Image.open(file_path) as img:
            image_format = img.format
            exif = img.getexif()
            width, height = img.size
            if str(exif.get(EXIF_ORIENTATION_TAG, "")) in ROTATED_ORIENTATIONS:
                width, height = height, width
            make = exif.get(EXIF_MAKE_TAG) or (asset.get("exifInfo") or {}).get("make")

            reason = check_dimensions(width, height, make, config)
            if reason:
                logging.info(f"Skipping {asset.get('originalFileName')}: {reason}.")
                os.remove(file_path)
                return None

            if image_format not in KODI_FORMATS:
                if not config["enable_heic_conversion"]:
                    logging.info(f"Keeping {image_format} file {file_path} unconverted (conversion disabled).")
                else:
                    file_path = _convert_to_jpeg(img, file_path)

        if config["write_location_caption"] and file_path.lower().endswith((".jpg", ".jpeg")):
            caption = location_caption(asset, config)
            if caption:
                _write_caption(file_path, caption)

        return file_path

    except Exception as e:
        logging.error(f"Error processing {file_path}: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return None


def _convert_to_jpeg(img, file_path):
    """Convert to JPEG with the rotation applied, keeping EXIF and the colour profile."""
    jpg_path = os.path.splitext(file_path)[0] + ".jpg"
    icc_profile = img.info.get("icc_profile")
    rotated = ImageOps.exif_transpose(img)
    exif = rotated.getexif()
    exif.pop(EXIF_ORIENTATION_TAG, None)
    rgb = rotated.convert("RGB")
    save_args = {"quality": 92, "exif": exif.tobytes()}
    if icc_profile:
        save_args["icc_profile"] = icc_profile
    rgb.save(jpg_path + ".tmp", "JPEG", **save_args)
    os.replace(jpg_path + ".tmp", jpg_path)
    if jpg_path != file_path:
        os.remove(file_path)
    logging.info(f"Converted {os.path.basename(file_path)} to JPEG.")
    return jpg_path


def _write_caption(file_path, caption):
    """Write the IPTC Caption-Abstract field, which Kodi's slideshow displays."""
    try:
        subprocess.run(
            [
                "exiftool", "-q", "-overwrite_original", "-charset", "iptc=UTF8",
                "-IPTC:CodedCharacterSet=UTF8", f"-IPTC:Caption-Abstract={caption}",
                f"-XMP-dc:Description={caption}", file_path,
            ],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        logging.warning(f"Failed to write caption to {file_path}: {e.stderr.strip()}")


# ------------------------------
# 5. Downloading
# ------------------------------

class Downloader:
    def __init__(self, client, config, staging_dir, executor):
        self.client = client
        self.config = config
        self.staging_dir = staging_dir
        self.executor = executor
        self.semaphore = asyncio.Semaphore(config["max_parallel_downloads"])
        self.seen_ids = set()  # shared across sources so overlapping people/albums are not duplicated

    async def collect(self, label, search, target):
        """Download up to `target` random images matching `search`."""
        kept = 0
        in_flight = 0
        empty_rounds = 0
        lock = asyncio.Lock()

        async def handle(asset):
            nonlocal kept, in_flight
            async with self.semaphore:
                async with lock:
                    if kept + in_flight >= target:
                        return
                    in_flight += 1
                ok = False
                try:
                    ok = await self._download_one(asset)
                finally:
                    async with lock:
                        in_flight -= 1
                        if ok:
                            kept += 1

        while kept < target and empty_rounds < MAX_EMPTY_ROUNDS:
            needed = target - kept
            # Ask for extra so rejected images do not each cost another round trip.
            assets = await self.client.random_assets(search, max(needed * 3, 50))
            candidates = []
            for asset in assets:
                if asset["id"] in self.seen_ids:
                    continue
                self.seen_ids.add(asset["id"])
                reason = prefilter_asset(asset, self.config)
                if reason:
                    logging.info(f"Skipping {asset.get('originalFileName')}: {reason}.")
                    continue
                candidates.append(asset)

            if not candidates:
                empty_rounds += 1
                continue
            empty_rounds = 0

            if self.config["dry_run"]:
                for asset in candidates[:needed]:
                    logging.info(f"[dry run] Would download {asset.get('originalFileName')} ({asset['id']}).")
                kept += min(needed, len(candidates))
                continue

            await asyncio.gather(*(handle(asset) for asset in candidates))

        if kept < target:
            logging.warning(f"{label}: only {kept} of {target} images matched the filters.")
        return kept

    async def _download_one(self, asset):
        url, params = self.client.download_url(asset, self.config["use_edited"])
        ext = ".jpg" if url.endswith("/thumbnail") else os.path.splitext(asset.get("originalFileName") or "")[1].lower()
        file_path = os.path.join(self.staging_dir, f"{asset['id']}{ext or '.jpg'}")
        try:
            await self.client.download(url, params, file_path)
        except Exception as e:
            logging.error(f"Error downloading asset {asset['id']}: {e}")
            return False

        loop = asyncio.get_running_loop()
        final_path = await loop.run_in_executor(self.executor, process_image, file_path, asset, self.config)
        if final_path:
            logging.info(f"Saved {asset.get('originalFileName')} as {os.path.basename(final_path)}.")
        return final_path is not None


# ------------------------------
# 6. Main Execution
# ------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Download a random selection of images from Immich.")
    parser.add_argument("--config", default=os.getenv("CONFIG_FILE", "config.yaml"), help="Path to the YAML config file")
    parser.add_argument("--output-dir", help="Directory to save downloaded images")
    parser.add_argument("--override", action="store_true", help="Override the safety check for the directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded without changing anything")
    return parser.parse_args()


async def run(config):
    output_dir = config["output_dir"]
    if not config["dry_run"]:
        check_directory(output_dir, config["override"])

    if config["write_location_caption"] and not shutil.which("exiftool"):
        logging.warning("exiftool not found; location captions are disabled.")
        config["write_location_caption"] = False

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=config["request_timeout"])
    async with aiohttp.ClientSession(timeout=timeout) as session:
        client = ImmichClient(session, config)
        try:
            version = await client.fetch_version()
        except Exception as e:
            logging.error(f"Cannot reach Immich at {config['immich_url']}: {e}")
            return 1
        logging.info(f"Connected to Immich v{'.'.join(map(str, version))}.")

        staging = None if config["dry_run"] else prepare_staging(output_dir)
        total = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=config["max_validation_workers"]) as executor:
            downloader = Downloader(client, config, staging, executor)
            target = config["total_images_to_download"]

            sources = [(f"person {pid}", {"person_id": pid}) for pid in config["person_ids"]]
            sources += [(f"album {aid}", {"album_id": aid}) for aid in config["album_ids"]]
            if not sources:
                sources = [("library", {})]

            for label, ids in sources:
                logging.info(f"Selecting {target} random images from {label}.")
                try:
                    count = await downloader.collect(label, client.build_search(config, **ids), target)
                except aiohttp.ClientResponseError as e:
                    hint = " Check that the API key has the asset.read, asset.view and asset.download permissions." if e.status in (401, 403) else ""
                    logging.error(f"Search failed for {label}: {e.status} {e.message}.{hint}")
                    continue
                except aiohttp.ClientError as e:
                    logging.error(f"Search failed for {label}: {e}")
                    continue
                logging.info(f"{'Found' if config['dry_run'] else 'Downloaded'} {count} images from {label}.")
                total += count

    if config["dry_run"]:
        logging.info(f"Dry run complete: {total} images would be downloaded. Nothing was changed.")
        return 0

    if total == 0:
        logging.error("No images were downloaded; keeping the previous selection.")
        _remove_path(staging)
        return 1

    swap_in_staging(output_dir, staging)
    logging.info(f"Done. {total} images are now in {output_dir}.")
    return 0


def main():
    args = parse_args()
    setup_logging(os.getenv("LOG_FILE", "immich_downloader.log"))
    try:
        config = load_config(args.config)
    except (yaml.YAMLError, ValueError, json.JSONDecodeError) as e:
        logging.error(f"Error parsing config file or environment variables: {e}")
        sys.exit(1)

    if args.output_dir:
        config["output_dir"] = args.output_dir
    config["override"] = config["override"] or args.override
    config["dry_run"] = config["dry_run"] or args.dry_run

    sys.exit(asyncio.run(run(config)))


if __name__ == "__main__":
    main()
