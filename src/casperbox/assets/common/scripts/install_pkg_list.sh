#!/usr/bin/env bash
# Install the apt packages listed in a package list file:
#   install_pkg_list.sh LIST
# One package per line, text after # is ignored. A trailing "?" marks an optional
# package: installed only if this OS release has it. An empty list installs nothing.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

pkg_list="${1:?usage: install_pkg_list.sh LIST}"
[[ -r "${pkg_list}" ]] || { echo "Error: package list not found: ${pkg_list}" >&2; exit 1; }
mapfile -t entries < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "${pkg_list}")
if (( ${#entries[@]} == 0 )); then
    echo "No packages in ${pkg_list}"
    exit 0
fi

bash "$(dirname "$0")/apt_update.sh"

packages=()
for entry in "${entries[@]}"; do
    if [[ "${entry}" == *"?" ]]; then
        pkg="${entry%"?"}"
        # Check for an installable version: `apt-cache show` also succeeds for a purely
        # virtual package (e.g. libasound2 on Ubuntu 24.04+, provided by libasound2t64),
        # which `apt-get install` then rejects. Such packages report "Candidate: (none)".
        candidate="$(apt-cache policy "${pkg}" 2>/dev/null | awk '/Candidate:/ {print $2}')"
        if [[ -n "${candidate}" && "${candidate}" != "(none)" ]]; then
            packages+=("${pkg}")
        else
            echo "Optional package ${pkg} is not available on this OS release, skipping"
        fi
    else
        packages+=("${entry}")
    fi
done

echo "Installing ${#packages[@]} packages from ${pkg_list}"
apt-get install -y --no-install-recommends "${packages[@]}"
apt-get clean
rm -rf /var/lib/apt/lists/*
