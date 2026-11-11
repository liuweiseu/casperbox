#!/usr/bin/env bash
# Fix for toolflow m2021a, run by run_patches.sh as root at the end of the image build (after the user,
# conda env and mlib_devel are set up). The build args are available as environment
# variables, e.g. CONTAINER_USER, TOOLFLOW_VERSION, VIVADO_VERSION.
set -euo pipefail

: "${CONTAINER_USER:?}"
bashrc="/home/${CONTAINER_USER}/.bashrc"

# Vivado 2021.1 crashes on Ubuntu 20.04 unless libudev is preloaded.
# Set in ~/.bashrc so that it applies to every terminal, also when vivado is run directly.
libudev=/lib/x86_64-linux-gnu/libudev.so.1
[[ -f "${libudev}" ]] || { echo "50-libudev.sh: ${libudev} not found" >&2; exit 1; }
if ! grep -qsF "${libudev}" "${bashrc}"; then
    cat >> "${bashrc}" <<BASHRC

# Vivado ${VIVADO_VERSION:-2021.1} needs libudev preloaded (casperbox versions/m2021a/patch/50-libudev.sh)
export LD_PRELOAD="${libudev}"
BASHRC
fi
echo "50-libudev.sh: LD_PRELOAD=${libudev} added to ${bashrc}"
