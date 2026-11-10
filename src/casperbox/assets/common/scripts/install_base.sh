#!/usr/bin/env bash
# Install the base system packages (common/packages/software.list, or the list given
# as argument) and set up the locale and sshd.
set -euo pipefail

pkg_list="${1:-/opt/casper/packages/software.list}"
bash "$(dirname "${BASH_SOURCE[0]}")/install_pkg_list.sh" "${pkg_list}"

# locale
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8

# sshd: no auditd in the container, make pam_loginuid optional to avoid login failures
mkdir -p /run/sshd
sed -i 's/^\(session\s\+\)required\(\s\+pam_loginuid.so\)/\1optional\2/' /etc/pam.d/sshd
