#!/bin/sh
set -eu
version=${1:?Usage: export-release.sh VERSION NEW_OUTPUT_DIRECTORY}
output=${2:?Supply a new output directory}
case "$version" in *[!a-zA-Z0-9._-]*|'') echo 'Invalid version' >&2; exit 1;; esac
cd "$(dirname "$0")/.."
test ! -e "$output" || { echo 'Output directory must not exist' >&2; exit 1; }
for image in "school-permit:$version" postgres:16-alpine; do
  docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}' | grep -qx 'linux/amd64'
done
mkdir -p "$output/license"
cp docker-compose.yml "$output/"
cp deployment/.env.example "$output/.env.example"
cp deployment/DEPLOYMENT.md "$output/DEPLOYMENT.md"
cp deployment/import-release.sh "$output/import-release.sh"
sed "s/^SCHOOL_PERMIT_VERSION=.*/SCHOOL_PERMIT_VERSION=$version/" "$output/.env.example" > "$output/.env.example.tmp"
mv "$output/.env.example.tmp" "$output/.env.example"
# Avoid masking docker-save failure; never export live container data.
docker save -o "$output/school-permit.tar" "school-permit:$version" postgres:16-alpine
gzip "$output/school-permit.tar"
(cd "$output" && sha256sum school-permit.tar.gz > SHA256SUMS)
echo 'Bundle exported. Add only customer license.lic to license/; never copy signing keys.'
