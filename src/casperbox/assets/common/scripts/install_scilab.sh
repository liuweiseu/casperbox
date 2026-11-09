#!/usr/bin/env bash
# Install Scilab:
#   1. Download the .tar.gz / .tar.xz archive at SCILAB_URL
#   2. Extract it to /opt/scilab (stripping the top-level scilab-<version>/ directory)
#   3. Link the executable to the container user's ~/.local/bin/scilab
# Runs as root, after create_user.sh.
set -euo pipefail

: "${SCILAB_VERSION:?SCILAB_VERSION is not set}"
: "${SCILAB_URL:?SCILAB_URL is not set}"
: "${CONTAINER_USER:?CONTAINER_USER is not set}"

install_dir=/opt/scilab
user_group="$(id -gn "${CONTAINER_USER}")"
user_bin="/home/${CONTAINER_USER}/.local/bin"

case "${SCILAB_URL}" in
    *.tar.gz|*.tgz|*.tar.xz) ;;
    *) echo "Error: SCILAB_URL must point to a .tar.gz or .tar.xz archive: ${SCILAB_URL}" >&2; exit 1 ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
archive="${tmp_dir}/$(basename "${SCILAB_URL}")"

echo "Downloading Scilab ${SCILAB_VERSION}: ${SCILAB_URL}"
curl -fL --retry 3 -o "${archive}" "${SCILAB_URL}"

# Extract to /opt/scilab (tar detects gz / xz automatically)
rm -rf "${install_dir}"
mkdir -p "${install_dir}"
tar -xf "${archive}" -C "${install_dir}" --strip-components=1

if [[ ! -x "${install_dir}/bin/scilab" ]]; then
    echo "Error: ${install_dir}/bin/scilab not found after extraction, please check the archive" >&2
    exit 1
fi

# Link to the user's ~/.local/bin/scilab (owned by the container user, so pip --user etc. work)
install -d -m 755 -o "${CONTAINER_USER}" -g "${user_group}" \
    "/home/${CONTAINER_USER}/.local" "${user_bin}"
ln -sfn "${install_dir}/bin/scilab" "${user_bin}/scilab"
chown -h "${CONTAINER_USER}:${user_group}" "${user_bin}/scilab"

echo "Scilab ${SCILAB_VERSION} installed to ${install_dir}, linked at ${user_bin}/scilab"
