#!/usr/bin/env bash
# 安装 Miniconda 到 ${CONDA_DIR}（默认 /opt/conda），并把属主交给容器用户
set -euo pipefail

: "${MINICONDA_INSTALLER:?MINICONDA_INSTALLER 未设置}"
: "${CONTAINER_USER:?CONTAINER_USER 未设置}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

curl -fL --retry 3 -o "${tmp_dir}/miniconda.sh" \
    "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}"
bash "${tmp_dir}/miniconda.sh" -b -p "${CONDA_DIR}"

# 只使用 conda-forge 频道，避免 Anaconda defaults 频道的服务条款限制
cat > "${CONDA_DIR}/.condarc" <<'EOF'
channels:
  - conda-forge
channel_priority: strict
auto_activate_base: false
EOF

"${CONDA_DIR}/bin/conda" clean -afy

# 让容器用户可以自行安装/升级包
chown -R "${CONTAINER_USER}:$(id -gn "${CONTAINER_USER}")" "${CONDA_DIR}"
