#!/usr/bin/env bash
# Install Miniconda to ${CONDA_DIR} (default /opt/conda) and hand ownership to the container user
set -euo pipefail

: "${MINICONDA_INSTALLER:?MINICONDA_INSTALLER is not set}"
: "${CONTAINER_USER:?CONTAINER_USER is not set}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

curl -fL --retry 3 -o "${tmp_dir}/miniconda.sh" \
    "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}"
bash "${tmp_dir}/miniconda.sh" -b -p "${CONDA_DIR}"

# Use conda-forge only, avoiding the Terms of Service restrictions of the Anaconda defaults channel
cat > "${CONDA_DIR}/.condarc" <<'EOF'
channels:
  - conda-forge
channel_priority: strict
auto_activate_base: false
EOF

"${CONDA_DIR}/bin/conda" clean -afy

# Let the container user install/upgrade packages
chown -R "${CONTAINER_USER}:$(id -gn "${CONTAINER_USER}")" "${CONDA_DIR}"
