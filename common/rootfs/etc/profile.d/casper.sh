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

# conda：默认激活 toolflow 的 python 环境（CONDA_ENV_NAME，例如 casper）
# 每个 shell 都要定义 conda 函数并执行 activate：
#   - 环境变量会从父进程继承，但 conda 函数和提示符 PS1 不会；
#   - 环境已激活时 conda 只做 reactivate，不会重复叠加，同时恢复 "(casper)" 提示符。
if [ -r /opt/conda/etc/profile.d/conda.sh ]; then
    # shellcheck disable=SC1091
    . /opt/conda/etc/profile.d/conda.sh
    conda activate "${CONDA_ENV_NAME}"
fi
