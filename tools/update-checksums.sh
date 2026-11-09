#!/usr/bin/env bash
# For maintainers: update SHA256SUMS after changing a Dockerfile or toolflow.conf under versions/<ver>/
set -euo pipefail

versions_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../versions" && pwd)"
cd "${versions_dir}"
sha256sum -- */Dockerfile */toolflow.conf > SHA256SUMS
echo "Updated ${versions_dir}/SHA256SUMS:"
cat SHA256SUMS
