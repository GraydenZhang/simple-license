#!/bin/sh
set -eu
cd "$(dirname "$0")"
sha256sum -c SHA256SUMS
docker load -i school-permit.tar.gz
echo 'Images imported. Configure .env and follow DEPLOYMENT.md.'
