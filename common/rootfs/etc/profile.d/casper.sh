# shellcheck shell=bash
# CASPER toolflow 环境：Vivado、mlib_devel(startsg.local)、conda 环境
# 由 /etc/profile（登录 shell）和 ~/.bashrc（交互 shell）加载，可重复加载。

# Vivado settings64.sh 等依赖 bash
[ -n "${BASH_VERSION:-}" ] || return 0
[ -r /etc/casper/toolflow.env ] || return 0

set -a
# shellcheck disable=SC1091
. /etc/casper/toolflow.env
set +a

export MLIB_DEVEL_PATH="${CASPER_TOOLFLOW_DIR}/mlib_devel"

# mlib_devel 本地配置（XILINX_PATH / JASPER_BACKEND / XLNX_DT_REPO_PATH 等）
if [ -r "${MLIB_DEVEL_PATH}/startsg.local" ]; then
    # shellcheck disable=SC1091
    . "${MLIB_DEVEL_PATH}/startsg.local"
fi

# Vivado（只加载一次，避免 PATH 重复增长）
_casper_vivado_settings="/tools/Xilinx/Vivado/${VIVADO_VERSION}/settings64.sh"
if [ -z "${CASPER_VIVADO_LOADED:-}" ] && [ -r "${_casper_vivado_settings}" ]; then
    # shellcheck disable=SC1090
    . "${_casper_vivado_settings}" >/dev/null
    export CASPER_VIVADO_LOADED=1
fi
unset _casper_vivado_settings

# conda：每个 shell 都需要定义 conda 函数，环境已激活时不重复激活
if [ -r /opt/conda/etc/profile.d/conda.sh ]; then
    # shellcheck disable=SC1091
    . /opt/conda/etc/profile.d/conda.sh
    if [ "${CONDA_DEFAULT_ENV:-}" != "${CONDA_ENV_NAME}" ]; then
        conda activate "${CONDA_ENV_NAME}"
    fi
fi
