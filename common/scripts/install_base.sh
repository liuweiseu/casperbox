#!/usr/bin/env bash
# Install the system packages listed in common/packages/software.list
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Package list: one package per line, # starts a comment
pkg_list="${1:-/opt/casper/packages/software.list}"
[[ -r "${pkg_list}" ]] || { echo "Error: package list not found: ${pkg_list}" >&2; exit 1; }
mapfile -t packages < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "${pkg_list}")
(( ${#packages[@]} > 0 )) || { echo "Error: no packages in ${pkg_list}" >&2; exit 1; }
echo "Installing ${#packages[@]} packages from ${pkg_list}"

apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"

# locale
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8

# sshd: no auditd in the container, make pam_loginuid optional to avoid login failures
mkdir -p /run/sshd
sed -i 's/^\(session\s\+\)required\(\s\+pam_loginuid.so\)/\1optional\2/' /etc/pam.d/sshd

apt-get clean
rm -rf /var/lib/apt/lists/*
