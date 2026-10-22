#!/usr/bin/env bash
# Installs SingularityCE 4.6 (Ubuntu 24.04 "noble" .deb) and downloads the MOFid image into external/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p external
DEB=external/singularity-ce_4.6.0-noble_amd64.deb
SIF=external/mofid.sif
if ! command -v singularity >/dev/null; then
  [ -f "$DEB" ] || curl -fL -o "$DEB" https://github.com/sylabs/singularity/releases/download/v4.6.0/singularity-ce_4.6.0-noble_amd64.deb
  sudo apt-get update
  sudo apt-get install -y "./$DEB"
fi
singularity --version
[ -f "$SIF" ] || curl -fL -o "$SIF" https://github.com/snurr-group/mofid/releases/download/v1.1.0/mofid.sif
echo "MOFid image: $SIF  (set MOFID_SIF in .env if you move it)"
