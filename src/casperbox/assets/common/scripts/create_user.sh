#!/usr/bin/env bash
# Create the container user: UID/GID match the host user, member of the sudo group.
# The password is not set at build time; the entrypoint sets it from CONTAINER_PWD at startup.
set -euo pipefail

: "${CONTAINER_USER:?CONTAINER_USER is not set}"
: "${USER_UID:?USER_UID is not set}"
: "${USER_GID:?USER_GID is not set}"

# If another user in the base image owns the UID (e.g. the ubuntu user in ubuntu:24.04+), remove it.
# Do this before handling the group: userdel may also delete that user's group.
if existing="$(getent passwd "${USER_UID}" | cut -d: -f1)" && [[ -n "${existing}" ]] \
        && [[ "${existing}" != "${CONTAINER_USER}" ]]; then
    echo "UID ${USER_UID} is used by ${existing}, removing that user"
    userdel -r "${existing}" 2>/dev/null || userdel "${existing}"
fi

# Group: reuse the GID if it already exists, otherwise create it
if getent group "${USER_GID}" >/dev/null; then
    group_name="$(getent group "${USER_GID}" | cut -d: -f1)"
else
    groupadd -g "${USER_GID}" "${CONTAINER_USER}"
    group_name="${CONTAINER_USER}"
fi

useradd -m -u "${USER_UID}" -g "${group_name}" -G sudo -s /bin/bash "${CONTAINER_USER}"

home_dir="/home/${CONTAINER_USER}"

# Pre-create mount points and work directories with correct ownership
# (CREATE_TOOLFLOW_DIRS=0 for casperbox-dev images, which have no toolflow).
# The VNC directory is created by the entrypoint: ~/.vnc or ~/.config/tigervnc, depending on the TigerVNC version.
if [[ "${CREATE_TOOLFLOW_DIRS:-1}" == 1 ]]; then
    mkdir -p "${home_dir}/work" "${home_dir}/.Xilinx" "${home_dir}/toolflow"
fi
chown -R "${USER_UID}:${USER_GID}" "${home_dir}"

# ---- Shell configuration ----
# ~/.bashrc normally comes from /etc/skel; write a minimal one if the base image has none
if [[ ! -f "${home_dir}/.bashrc" ]]; then
    cat > "${home_dir}/.bashrc" <<'EOF'
# ~/.bashrc: executed by bash for interactive shells
case $- in *i*) ;; *) return ;; esac
HISTCONTROL=ignoreboth
shopt -s histappend checkwinsize
PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
alias ls='ls --color=auto'
alias grep='grep --color=auto'
alias ll='ls -alF'
EOF
fi
# Coloured prompt whenever the terminal supports colours (not only for TERM=*-256color)
sed -i 's/^#force_color_prompt=yes/force_color_prompt=yes/' "${home_dir}/.bashrc"

# Non-login shells (e.g. terminals opened in VNC) also load the toolflow environment
cat >> "${home_dir}/.bashrc" <<'EOF'

# CASPER toolflow environment (Vivado / conda / mlib_devel)
[ -r /etc/profile.d/casper.sh ] && . /etc/profile.d/casper.sh
EOF

# Login shells (SSH, `casperbox shell`) read ~/.profile: make sure it loads ~/.bashrc
if ! grep -qs '\.bashrc' "${home_dir}/.profile"; then
    cat >> "${home_dir}/.profile" <<'EOF'

# Load ~/.bashrc in login shells too
if [ -n "$BASH_VERSION" ] && [ -f "$HOME/.bashrc" ]; then
    . "$HOME/.bashrc"
fi
EOF
fi
chown "${USER_UID}:${USER_GID}" "${home_dir}/.bashrc" "${home_dir}/.profile"
