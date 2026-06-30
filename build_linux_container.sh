#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_NAME="${IMAGE_NAME:-mediarunner-linux-builder}"
PLATFORM="${PLATFORM:-linux/amd64}"
APP_NAME="MediaRunner"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed or not on PATH." >&2
  echo "Install Docker, or run ./build_linux.sh directly on a Linux host." >&2
  exit 2
fi

docker build --platform "$PLATFORM" -f Dockerfile.linux -t "$IMAGE_NAME" .

container_id="$(docker create "$IMAGE_NAME")"
trap 'docker rm -f "$container_id" >/dev/null 2>&1 || true' EXIT

rm -rf ./dist-linux
mkdir -p ./dist-linux ./dist
docker cp "$container_id:/work/dist/${APP_NAME}" "./dist-linux/${APP_NAME}"

archive_platform="${PLATFORM//\//-}"
tar -C ./dist-linux -czf "./dist/${APP_NAME}-${archive_platform}.tar.gz" "${APP_NAME}"

echo "Container build copied to:"
echo "  $PWD/dist-linux/${APP_NAME}/${APP_NAME}"
echo "Archive:"
echo "  $PWD/dist/${APP_NAME}-${archive_platform}.tar.gz"
