#!/usr/bin/env bash
# Install conda (Miniforge) to ${CONDA_DIR} (default /opt/conda) and hand ownership to the container user
#   CONDA_INSTALLER_URL: URL of a conda installer script (Miniforge3-Linux-x86_64.sh, or a mirror of it)
set -euo pipefail

: "${CONDA_INSTALLER_URL:?CONDA_INSTALLER_URL is not set}"
: "${CONTAINER_USER:?CONTAINER_USER is not set}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

curl -fL --retry 3 -o "${tmp_dir}/installer.sh" "${CONDA_INSTALLER_URL}"
bash "${tmp_dir}/installer.sh" -b -p "${CONDA_DIR}"

# Use conda-forge only (Miniforge's default), never the Anaconda defaults channel and its Terms of Service
cat > "${CONDA_DIR}/.condarc" <<'EOF'
channels:
  - conda-forge
channel_priority: strict
auto_activate_base: false
EOF

"${CONDA_DIR}/bin/conda" clean -afy

# Let the container user install/upgrade packages
chown -R "${CONTAINER_USER}:$(id -gn "${CONTAINER_USER}")" "${CONDA_DIR}"
