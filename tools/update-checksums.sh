#!/usr/bin/env bash
# 维护者使用：修改 versions/<ver>/ 下的 Dockerfile 或 toolflow.conf 后更新 SHA256SUMS
set -euo pipefail

versions_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../versions" && pwd)"
cd "${versions_dir}"
sha256sum -- */Dockerfile */toolflow.conf > SHA256SUMS
echo "已更新 ${versions_dir}/SHA256SUMS:"
cat SHA256SUMS
