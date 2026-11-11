#!/usr/bin/env bash
# Run the version-specific fixes at the end of the image build:
#   run_patches.sh DIR
# Every *.sh in DIR is run with bash, as root, in file name order (use number prefixes such as
# 10-xxx.sh, 50-yyy.sh to set the order), with DIR as the working directory. Other files in DIR
# are ignored. A failing script fails the build. The build args are environment variables.
set -euo pipefail

dir="${1:?usage: run_patches.sh DIR}"
export DEBIAN_FRONTEND=noninteractive
LC_COLLATE=C  # plain byte order for the file names

shopt -s nullglob
scripts=("${dir}"/*.sh)
if (( ${#scripts[@]} == 0 )); then
    echo "No patch scripts in versions/${TOOLFLOW_VERSION:-<version>}/patch"
    exit 0
fi

# The package indexes were removed after the package installs; scripts may apt-get install
bash "$(dirname "$0")/apt_update.sh"

for script in "${scripts[@]}"; do
    name="$(basename "${script}")"
    echo "==> patch/${name}"
    if ! (cd "${dir}" && bash "${script}"); then
        echo "Error: patch/${name} failed" >&2
        exit 1
    fi
done

apt-get clean
rm -rf /var/lib/apt/lists/*
