#!/usr/bin/env bash
# Create conda env CONDA_ENV_NAME with PYTHON_VERSION and install the extra conda packages
# (runs as the container user)
set -euo pipefail

: "${PYTHON_VERSION:?PYTHON_VERSION is not set}"
: "${CONDA_ENV_NAME:?CONDA_ENV_NAME is not set}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"

# shellcheck disable=SC1091
source "${CONDA_DIR}/etc/profile.d/conda.sh"

conda create -y -n "${CONDA_ENV_NAME}" --override-channels -c conda-forge \
    "python=${PYTHON_VERSION}" pip

# Extra packages used by the toolflow tools (GUI plots, terminal output)
conda_packages=(pyqt6 pyqtgraph rich)
echo ">>> Installing ${conda_packages[*]} into conda env ${CONDA_ENV_NAME}"
conda install -y -n "${CONDA_ENV_NAME}" --override-channels -c conda-forge "${conda_packages[@]}"
conda clean -afy

echo "conda env ${CONDA_ENV_NAME} (python ${PYTHON_VERSION}) created"
