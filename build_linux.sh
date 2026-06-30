#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="MediaRunner"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USE_CURRENT_PYTHON="${MEDIARUNNER_USE_CURRENT_PYTHON:-0}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: PyInstaller must build Linux executables on Linux." >&2
  echo "Run this script on a Linux host, or use ./build_linux_container.sh on a Docker-capable machine." >&2
  exit 2
fi

BUILD_MARKER="$(grep -m1 '^MEDIARUNNER_BUILD_ID' mediarunner_gui.py | sed 's/.*= *"\(.*\)"/\1/')"
if [[ -z "$BUILD_MARKER" || "$BUILD_MARKER" != MediaRunner\ Version* ]]; then
  echo "ERROR: mediarunner_gui.py is missing a valid MEDIARUNNER_BUILD_ID line" >&2
  exit 1
fi
echo "Building Linux package: $BUILD_MARKER"

if [[ "$USE_CURRENT_PYTHON" != "1" ]]; then
  if [[ ! -d .venv-linux ]]; then
    "$PYTHON_BIN" -m venv .venv-linux
  fi
  # shellcheck disable=SC1091
  source .venv-linux/bin/activate
fi

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-linux.txt
python verify_install.py

rm -rf ./build ./dist ./.pyinstaller-cache
mkdir -p ./.pyinstaller-cache

env PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache" python -m PyInstaller \
  --noconfirm \
  MediaRunner.linux.spec

chmod +x "dist/${APP_NAME}/${APP_NAME}"

cat <<EOF
Build complete:
  $PWD/dist/${APP_NAME}/${APP_NAME}

Smoke test on a Linux desktop:
  "$PWD/dist/${APP_NAME}/${APP_NAME}"

Archive command:
  tar -C "$PWD/dist" -czf "$PWD/dist/${APP_NAME}-linux-$(uname -m).tar.gz" "${APP_NAME}"
EOF
