
# Immich Downloader

Immich Downloader pulls a random selection of images from an Immich server into a local folder. You can pick images of specific people, from specific albums, or from your whole library, and filter out screenshots, small images and photos outside a date range. Configure it with YAML, environment variables or command-line flags, and run it on bare metal or in Docker. It does one run and exits, so schedule it with cron or similar (see [Scheduling](#scheduling)).

Works with Immich v2.0 and newer, including the v3.2+ search API.

---

## Why?

My entertainment center is run on a kodi box. I have wanted for a long time to set up a screensaver that would show photos of my family from my Immich library, taking advantage of its awesome facial recognition. I have been using Immich Kiosk on tablets which does this very well, but could not get it or anything like it setup as a screensaver on Kodi. Kodi does have a built in picture slideshow screensaver, however, which can be pointed at a local folder. I needed a way to import photos to that folder per person or album. That is the purpose of this script. I did not want to download every photo available to avoid A) using up disk space and B) because I want the most recent photos to seamlessly be inserted into the slideshow. Hence, this script. I have it set up to run every few hours and to download 200 photos at random. Those photos are seen from the slideshow and after another few hours theres a different set with as much a chance of seeing a recent photo as a very old one. That is why the script is not intended to perfectly mirror a person id or album id and why the old photos are deleted with each run. I am sure there could be other uses for this script, but this is why I created it and it has so far worked very well for me.

---

## Features

- Random images by **person ID**, **album ID**, or from the whole library, using Immich's random search, so every photo has the same chance of being picked.
- Filters by size, megapixels, screenshot dimensions, date and archive status. These run on Immich's metadata before downloading, so rejected images are never downloaded.
- Safe replacement: the new selection is downloaded into a hidden staging folder and swapped in at the end. Kodi never sees an empty or half-filled folder, and if Immich is unreachable the previous selection stays.
- Converts HEIC/AVIF/WebP/TIFF to JPEG with the correct rotation. RAW files are fetched as Immich's full-size JPEG.
- Downloads the edited version of photos you've edited in Immich (Immich 2.5+).
- Optional location captions: writes the place (e.g. "Boston, Massachusetts") from Immich's own reverse geocoding into the IPTC caption, which Kodi's Picture Slideshow screensaver can show. No Nominatim server needed.
- Safety marker so it never wipes a folder it didn't create (unless overridden), and a dry-run mode.

---

## Immich API key

Create an API key in Immich under **Account Settings → API Keys**. If you give it limited permissions, it needs:

- `asset.read` (random search)
- `asset.download` (original files)
- `asset.view` (full-size renditions of RAW files)

Person and album IDs are the last part of the URL when you open the person or album in the Immich web UI.

---

## Running on Bare Metal

1. Clone the repository:

   ```bash
   git clone https://github.com/jon6fingrs/immich-dl.git
   cd immich-dl
   ```

2. Install exiftool (only needed for location captions):

   ```bash
   sudo apt-get install -y libimage-exiftool-perl
   ```

3. Install the Python dependencies (Python 3.9+):

   ```bash
   pip install -r requirements.txt
   ```

4. Copy `config.yaml.example` to `config.yaml` and fill it in.

5. Run it:

   ```bash
   python3 immich-dl.py                 # uses ./config.yaml
   python3 immich-dl.py --dry-run       # show what would be downloaded
   python3 immich-dl.py --config /etc/immich-dl.yaml --output-dir /srv/kodi/screensaver
   ```

Any option can be overridden with an environment variable.

---

## Running with Docker

The prebuilt image is on Docker Hub: [thehelpfulidiot/immich-dl](https://hub.docker.com/r/thehelpfulidiot/immich-dl).

Use `latest` to follow updates, or pin a version such as `thehelpfulidiot/immich-dl:2.0`. Version 2.0 is the first release for the current Immich API (v2.0+, including v3.2+) and changes some defaults (see [#2](https://github.com/jon6fingrs/immich-dl/pull/2)).

### `docker run`

```bash
docker run --rm \
  -e IMMICH_URL=http://your-immich-server:2283 \
  -e API_KEY=your-api-key \
  -e TOTAL_IMAGES_TO_DOWNLOAD=200 \
  -e PERSON_IDS='["person-id-1", "person-id-2"]' \
  -e MIN_WIDTH=1000 \
  -e MIN_HEIGHT=800 \
  -e SCREENSHOT_DIMENSIONS='[[1170, 2532], [1920, 1080]]' \
  -e WRITE_LOCATION_CAPTION=true \
  -v /path/to/kodi/screensaver:/downloads \
  thehelpfulidiot/immich-dl:latest
```

### Docker Compose

See [`docker-compose.yaml`](docker-compose.yaml) for every option. Then:

```bash
docker compose run --rm immich-dl
```

To use a YAML file instead of environment variables, mount it and point `CONFIG_FILE` at it:

```yaml
    environment:
      CONFIG_FILE: /config/config.yaml
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - ./downloads:/downloads
```

### Image publishing

A GitHub Actions workflow (`.github/workflows/docker-publish.yml`) builds the image for `linux/amd64` and `linux/arm64` and pushes it to Docker Hub. Tags:

- `latest` on every merge to `main`
- `1.2.3` and `1.2` when a `v1.2.3` tag is pushed
- `sha-<commit>` for every published build

To release a new version, bump `__version__` in `immich-dl.py`, merge, then push a matching tag (`git tag v2.0.1 && git push origin v2.0.1`). Pull requests are built as a check but not pushed. Publishing needs the `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` repository secrets.

### Building the image locally

```bash
docker build -t immich-dl:latest .
```

---

## Scheduling

The script does one run and exits. For example, to refresh the selection every night at 3am, add this to your crontab (`crontab -e`):

```cron
0 3 * * * cd /path/to/immich-dl && python3 immich-dl.py >> /var/log/immich-dl.cron.log 2>&1
```

or with Docker Compose:

```cron
0 3 * * * cd /path/to/immich-dl && docker compose run --rm immich-dl
```

The script exits with a non-zero code if it couldn't download anything (the previous images are kept), so cron's error mail or your monitoring will notice.

---

## Kodi setup

1. Point the output folder at a location Kodi can read (a local folder, or an SMB/NFS share).
2. In Kodi, go to **Settings → Interface → Screensaver**, choose **Picture Slideshow**, set the source to **Image folder** and pick the folder.
3. To show the location, set `WRITE_LOCATION_CAPTION=true` and turn on the screensaver's option to display the image caption/info.

The staging folder (`.immich-dl-staging`) is hidden, so Kodi ignores it unless "Show hidden files" is on.

---

## Configuration Options

Every option can be set in YAML, or as an environment variable with the same name in upper case. Environment variables win over YAML.

| **Option** | **Environment Variable** | **YAML Key** | **Flag** | **Description** |
|---|---|---|---|---|
| Immich Server URL | `IMMICH_URL` | `immich_url` | | Base URL of the Immich server. **Required.** |
| API Key | `API_KEY` | `api_key` | | Immich API key. **Required.** |
| Config File | `CONFIG_FILE` | | `--config` | Path to the YAML file. Default `config.yaml`. |
| Output Directory | `OUTPUT_DIR` | `output_dir` | `--output-dir` | Folder for the images. Its contents are replaced each run. Default `/downloads`. |
| Images to Download | `TOTAL_IMAGES_TO_DOWNLOAD` | `total_images_to_download` | | Number of images per person and per album, or from the whole library. Default 10. |
| Person IDs | `PERSON_IDS` | `person_ids` | | JSON list of person IDs. |
| Album IDs | `ALBUM_IDS` | `album_ids` | | JSON list of album IDs (your own or shared with you). |
| Minimum Megapixels | `MIN_MEGAPIXELS` | `min_megapixels` | | Skip images below this many megapixels. |
| Minimum Width | `MIN_WIDTH` | `min_width` | | Skip images narrower than this, measured after rotation. |
| Minimum Height | `MIN_HEIGHT` | `min_height` | | Skip images shorter than this, measured after rotation. |
| Screenshot Dimensions | `SCREENSHOT_DIMENSIONS` | `screenshot_dimensions` | | JSON list of `[width, height]`. Images of exactly that size with no camera make are skipped. |
| Minimum Date | `MIN_DATE` | `min_date` | | Only photos taken on or after this date (`YYYY-MM-DD`). |
| Maximum Date | `MAX_DATE` | `max_date` | | Only photos taken on or before this date (`YYYY-MM-DD`). |
| Include Archived | `INCLUDE_ARCHIVED` | `include_archived` | | Also pick archived photos. Default false. |
| Use Edited Versions | `USE_EDITED` | `use_edited` | | Download the edited version of photos edited in Immich (Immich 2.5+). Default true. |
| Convert to JPEG | `ENABLE_HEIC_CONVERSION` | `enable_heic_conversion` | | Convert HEIC/AVIF/WebP/TIFF to JPEG. Default true. |
| Location Captions | `WRITE_LOCATION_CAPTION` | `write_location_caption` | | Write the photo's location into the IPTC caption. Requires exiftool. Default false. |
| Countries Left Out of Captions | `CAPTION_OMIT_COUNTRIES` | `caption_omit_countries` | | JSON list. Default `["United States of America", "United States"]`. |
| Max Parallel Downloads | `MAX_PARALLEL_DOWNLOADS` | `max_parallel_downloads` | | Default 5. |
| Validation Workers | `MAX_VALIDATION_WORKERS` | `max_validation_workers` | | Threads for checking and converting images. Default 4. |
| Request Timeout | `REQUEST_TIMEOUT` | `request_timeout` | | Seconds to wait for data from Immich. Default 300. |
| Override Safety Check | `OVERRIDE` | `override` | `--override` | Replace the folder's contents even without the marker file. Default false. |
| Dry Run | `DRY_RUN` | `dry_run` | `--dry-run` | Log what would be downloaded without changing anything. Default false. |
| Log File | `LOG_FILE` | | | Rotating log file. Default `immich_downloader.log`; set empty to disable. |

`MAX_HEIC_CONVERSION_WORKERS` from older versions is no longer used and is ignored.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.
