# shellcheck shell=bash
# CASPER toolflow environment: Vivado, mlib_devel (startsg.local), conda env
# Sourced by /etc/profile (login shells) and ~/.bashrc (interactive shells); safe to source repeatedly.

# Vivado settings64.sh etc. require bash
[ -n "${BASH_VERSION:-}" ] || return 0
[ -r /etc/casper/toolflow.env ] || return 0

set -a
# shellcheck disable=SC1091
. /etc/casper/toolflow.env
# Generated at container startup: VIVADO_DIR / XILINX_PATH / COMPOSER_PATH
# shellcheck disable=SC1091
[ -r /etc/casper/runtime.env ] && . /etc/casper/runtime.env
set +a

export MLIB_DEVEL_PATH="${CASPER_TOOLFLOW_DIR}/mlib_devel"

# ~/.local/bin (scilab etc. are linked here)
case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac

# mlib_devel local config (XILINX_PATH / JASPER_BACKEND / XLNX_DT_REPO_PATH etc.)
if [ -r "${MLIB_DEVEL_PATH}/startsg.local" ]; then
    # shellcheck disable=SC1091
    . "${MLIB_DEVEL_PATH}/startsg.local"
fi

# Vivado (load once to avoid growing PATH repeatedly)
_casper_vivado_settings="${XILINX_PATH:-}/settings64.sh"
if [ -z "${CASPER_VIVADO_LOADED:-}" ] && [ -n "${XILINX_PATH:-}" ] && [ -r "${_casper_vivado_settings}" ]; then
    # shellcheck disable=SC1090
    . "${_casper_vivado_settings}" >/dev/null
    export CASPER_VIVADO_LOADED=1
fi
unset _casper_vivado_settings

# conda: activate the toolflow python env by default (CONDA_ENV_NAME, e.g. casper)
# Every shell must define the conda function and run activate:
#   - environment variables are inherited from the parent, but the conda function and PS1 are not;
#   - if the env is already active, conda only reactivates (no stacking) and restores the "(casper)" prompt.
if [ -r /opt/conda/etc/profile.d/conda.sh ]; then
    # shellcheck disable=SC1091
    . /opt/conda/etc/profile.d/conda.sh
    conda activate "${CONDA_ENV_NAME}"
fi
