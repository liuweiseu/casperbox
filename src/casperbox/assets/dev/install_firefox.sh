#!/usr/bin/env bash
# Install Firefox from Mozilla's APT repository (https://packages.mozilla.org).
# The distribution's own "firefox" package is a snap wrapper on Ubuntu 22.04+, and snaps
# do not run in containers; Mozilla's repository provides a real .deb for Ubuntu and Debian.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

install -d -m 0755 /etc/apt/keyrings
curl -fsSL --retry 3 https://packages.mozilla.org/apt/repo-signing-key.gpg \
    -o /etc/apt/keyrings/packages.mozilla.org.asc

echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
    > /etc/apt/sources.list.d/mozilla.list

# Prefer Mozilla's firefox over the distribution's snap wrapper
cat > /etc/apt/preferences.d/mozilla <<'PREF'
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
PREF

apt-get update
apt-get install -y --no-install-recommends firefox
apt-get clean
rm -rf /var/lib/apt/lists/*

firefox --version
