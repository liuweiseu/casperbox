#!/usr/bin/env bash
# Install the extra apt packages from `packages` in casper-dev.toml (passed as arguments).
set -euo pipefail

if (( $# == 0 )); then
    echo "No extra packages"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
echo "Installing extra packages: $*"
bash "$(dirname "$0")/apt_update.sh"
apt-get install -y --no-install-recommends "$@"
apt-get clean
rm -rf /var/lib/apt/lists/*
