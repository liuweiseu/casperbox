#!/usr/bin/env bash
# Run the install scripts of [[tools]] from casperbox-dev.toml:
#
#   install_tools.sh root|user DIR
#
# DIR holds NN-<name>.sh (the script) and NN-<name>.version for each tool, written by casperbox-dev in
# config order. Tools run one after the other and a failing script stops the build.
#
# Environment of a script: VERSION (the tool's `version`), DEB_ARCH (amd64 | arm64), CONTAINER_USER,
# HOME and PATH (with ~/.local/bin first). `root` tools run as root, `user` tools as the container user.
set -euo pipefail

run_as="${1:?usage: install_tools.sh root|user DIR}"
dir="${2:?usage: install_tools.sh root|user DIR}"
: "${CONTAINER_USER:?CONTAINER_USER is not set}"

shopt -s nullglob
scripts=("${dir}"/*.sh)
if (( ${#scripts[@]} == 0 )); then
    echo "No ${run_as} tools"
    exit 0
fi

deb_arch="$(dpkg --print-architecture)"
for script in "${scripts[@]}"; do
    name="$(basename "${script}" .sh)"
    version="$(cat "${dir}/${name}.version")"
    echo "==> tool ${name#*-} (${run_as}${version:+, version ${version}})"
    if [[ "${run_as}" == root ]]; then
        env HOME=/root VERSION="${version}" DEB_ARCH="${deb_arch}" bash "${script}"
    else
        home="/home/${CONTAINER_USER}"
        runuser -u "${CONTAINER_USER}" -- env \
            HOME="${home}" USER="${CONTAINER_USER}" LOGNAME="${CONTAINER_USER}" \
            PATH="${home}/.local/bin:${PATH}" \
            CONTAINER_USER="${CONTAINER_USER}" VERSION="${version}" DEB_ARCH="${deb_arch}" \
            bash "${script}"
    fi
done
