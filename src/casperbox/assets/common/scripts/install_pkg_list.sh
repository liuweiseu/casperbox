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

apt-get update

packages=()
for entry in "${entries[@]}"; do
    if [[ "${entry}" == *"?" ]]; then
        pkg="${entry%"?"}"
        if apt-cache show "${pkg}" >/dev/null 2>&1; then
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
