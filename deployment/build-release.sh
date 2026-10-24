#!/bin/sh
set -eu
version=${1:?Usage: build-release.sh VERSION}
case "$version" in *[!a-zA-Z0-9._-]*|'') echo 'Invalid version' >&2; exit 1;; esac
cd "$(dirname "$0")/.."
docker buildx build --platform linux/amd64 --load -t "school-permit:$version" .
docker image inspect "school-permit:$version" --format '{{.Os}}/{{.Architecture}}' | grep -qx 'linux/amd64'
echo "Built school-permit:$version. Run deployment/export-release.sh after runtime acceptance."
