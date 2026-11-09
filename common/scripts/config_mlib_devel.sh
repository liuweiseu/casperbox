#!/usr/bin/env bash
# 配置 mlib_devel（以容器用户身份运行）：
#   1. 克隆 mlib_devel 与 device-tree-xlnx 到 ~/toolflow
#   2. 在 conda 环境中安装 mlib_devel 依赖的 python 库
#   3. 生成 mlib_devel/startsg.local
set -euo pipefail

: "${MLIB_DEVEL_REPO:?}" "${MLIB_DEVEL_BRANCH:?}"
: "${DEVICE_TREE_XLNX_REPO:?}" "${DEVICE_TREE_XLNX_BRANCH:?}"
: "${CONDA_ENV_NAME:?}" "${VIVADO_VERSION:?}"
CONDA_DIR="${CONDA_DIR:-/opt/conda}"
GIT_CLONE_DEPTH="${GIT_CLONE_DEPTH:-1}"

toolflow_dir="${HOME}/toolflow"
mlib_dir="${toolflow_dir}/mlib_devel"
dt_dir="${toolflow_dir}/device-tree-xlnx"

depth_args=()
if [[ "${GIT_CLONE_DEPTH}" != "0" ]]; then
    depth_args=(--depth "${GIT_CLONE_DEPTH}")
fi

mkdir -p "${toolflow_dir}"

echo ">>> 克隆 mlib_devel (${MLIB_DEVEL_BRANCH})"
git clone "${depth_args[@]}" --single-branch --branch "${MLIB_DEVEL_BRANCH}" \
    "${MLIB_DEVEL_REPO}" "${mlib_dir}"

echo ">>> 克隆 device-tree-xlnx (${DEVICE_TREE_XLNX_BRANCH})"
git clone "${depth_args[@]}" --single-branch --branch "${DEVICE_TREE_XLNX_BRANCH}" \
    "${DEVICE_TREE_XLNX_REPO}" "${dt_dir}"

echo ">>> 在 conda 环境 ${CONDA_ENV_NAME} 中安装 mlib_devel 依赖"
# shellcheck disable=SC1091
source "${CONDA_DIR}/etc/profile.d/conda.sh"
conda activate "${CONDA_ENV_NAME}"
python -m pip install --no-cache-dir --upgrade pip
# requirements.txt 中有 -e git+... 形式的依赖，源码放到 ~/toolflow/src
(cd "${mlib_dir}" && python -m pip install --no-cache-dir --src "${toolflow_dir}/src" -r requirements.txt)

echo ">>> 生成 ${mlib_dir}/startsg.local"
cat > "${mlib_dir}/startsg.local" <<EOF
# 由 casper-container 在构建镜像时生成
# Vivado 挂载路径与宿主机一致，容器启动时由 entrypoint 写入 /etc/casper/runtime.env
[ -r /etc/casper/runtime.env ] && . /etc/casper/runtime.env
export XILINX_PATH COMPOSER_PATH
export PLATFORM=lin64
export JASPER_BACKEND=vivado
export XLNX_DT_REPO_PATH=${dt_dir}
#export MATLAB_PATH=/path/to/matlab
# python 环境：登录 shell 会通过 /etc/profile.d/casper.sh 自动激活 conda 环境 ${CONDA_ENV_NAME}
EOF

echo "mlib_devel 配置完成：${mlib_dir}"
