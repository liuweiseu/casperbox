#!/usr/bin/env bash
# Create the container user: UID/GID match the host user, member of the sudo group.
# The password is not set at build time; the entrypoint sets it from CONTAINER_PWD at startup.
set -euo pipefail

: "${CONTAINER_USER:?CONTAINER_USER is not set}"
: "${USER_UID:?USER_UID is not set}"
: "${USER_GID:?USER_GID is not set}"

# Group: reuse the GID if it already exists, otherwise create it
if getent group "${USER_GID}" >/dev/null; then
    group_name="$(getent group "${USER_GID}" | cut -d: -f1)"
else
    groupadd -g "${USER_GID}" "${CONTAINER_USER}"
    group_name="${CONTAINER_USER}"
fi

# If another user in the base image owns the UID (e.g. the ubuntu user), remove it
if existing="$(getent passwd "${USER_UID}" | cut -d: -f1)" && [[ -n "${existing}" ]] \
        && [[ "${existing}" != "${CONTAINER_USER}" ]]; then
    echo "UID ${USER_UID} is used by ${existing}, removing that user"
    userdel -r "${existing}" 2>/dev/null || userdel "${existing}"
fi

useradd -m -u "${USER_UID}" -g "${group_name}" -G sudo -s /bin/bash "${CONTAINER_USER}"

home_dir="/home/${CONTAINER_USER}"

# Pre-create mount points and work directories with correct ownership
mkdir -p "${home_dir}/work" "${home_dir}/.Xilinx" "${home_dir}/toolflow" "${home_dir}/.vnc"
chown -R "${USER_UID}:${USER_GID}" "${home_dir}"

# Non-login shells (e.g. terminals opened in VNC) also load the toolflow environment
cat >> "${home_dir}/.bashrc" <<'EOF'

# CASPER toolflow environment (Vivado / conda / mlib_devel)
[ -r /etc/profile.d/casper.sh ] && . /etc/profile.d/casper.sh
EOF
chown "${USER_UID}:${USER_GID}" "${home_dir}/.bashrc"
