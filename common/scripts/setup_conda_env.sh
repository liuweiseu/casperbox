#!/usr/bin/env bash
# 按 PYTHON_VERSION 创建 conda 环境 CONDA_ENV_NAME（以容器用户身份运行）
set -euo pipefail

: "${PYTHON_VERSION:?PYTHON_VERSION 未设置}"
: "${CONDA_ENV_NAME:?CONDA_ENV_NAME 未设置}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"

# shellcheck disable=SC1091
source "${CONDA_DIR}/etc/profile.d/conda.sh"

conda create -y -n "${CONDA_ENV_NAME}" --override-channels -c conda-forge \
    "python=${PYTHON_VERSION}" pip
conda clean -afy

echo "conda 环境 ${CONDA_ENV_NAME} (python ${PYTHON_VERSION}) 已创建"
