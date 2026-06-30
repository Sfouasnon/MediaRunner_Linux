#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="MediaRunner"

# Dynamic build marker: read MEDIARUNNER_BUILD_ID from the source instead of a
# hardcoded version string, so this check never goes stale on a version bump.
BUILD_MARKER="$(grep -m1 '^MEDIARUNNER_BUILD_ID' mediarunner_gui.py | sed 's/.*= *"\(.*\)"/\1/')"
if [[ -z "$BUILD_MARKER" || "$BUILD_MARKER" != MediaRunner\ Version* ]]; then
  echo "ERROR: mediarunner_gui.py is missing a valid MEDIARUNNER_BUILD_ID line" >&2
  exit 1
fi
echo "Building: $BUILD_MARKER"

osascript -e 'quit app "MediaRunner"' 2>/dev/null || true

rm -rf ./build ./dist ./MediaRunner.spec
mkdir -p ./.pyinstaller-cache

env PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache" nice -n 10 python3 -m PyInstaller \
  --noconfirm \
  --onedir \
  --windowed \
  --name "$APP_NAME" \
  --exclude-module scipy \
  --exclude-module matplotlib \
  --exclude-module pyarrow \
  --exclude-module PIL \
  --exclude-module jinja2 \
  --exclude-module IPython \
  --exclude-module notebook \
  --add-data "assets:assets" \
  --add-data "validation:validation" \
  --add-data "MediaRunner_LOGO.png:." \
  --add-data "MediaRunner_LOGO_HTML.png:." \
  --add-data "MediaRunner_REPORT_LOGO.png:." \
  --add-data "mediarunner_core.py:." \
  --add-data "mediarunner_ftp.py:." \
  --add-data "mediarunner_transfer.py:." \
  --add-data "mediarunner_meta.py:." \
  --add-data "mediarunner_reports.py:." \
  --add-data "mediarunner_red_wireless.py:." \
  --add-data "mediarunner_mhl.py:." \
  --add-data "mediarunner_logging.py:." \
  --add-data "mediarunner_notifications.py:." \
  --add-data "mediarunner_linux_ingest.py:." \
  mediarunner_gui.py

echo "Build complete: dist/${APP_NAME}.app"
