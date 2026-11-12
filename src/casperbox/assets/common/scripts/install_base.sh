#!/usr/bin/env bash
# Install the base system packages (common/packages/software.list, or the list given
# as argument) and set up the locale and sshd.
set -euo pipefail

pkg_list="${1:-/opt/casper/packages/software.list}"
bash "$(dirname "${BASH_SOURCE[0]}")/install_pkg_list.sh" "${pkg_list}"

# The entrypoint needs these; fail the build instead of starting a container without VNC.
# Debian/Ubuntu name them vncpasswd/vncserver (alternatives) or tigervncpasswd/tigervncserver.
for cmds in "vncpasswd tigervncpasswd" "vncserver tigervncserver"; do
    found=""
    for c in ${cmds}; do
        command -v "${c}" >/dev/null && found="${c}" && break
    done
    if [[ -z "${found}" ]]; then
        echo "Error: none of ${cmds} found after installing ${pkg_list}" >&2
        echo "       (vncpasswd is in tigervnc-tools on Ubuntu 22.04+, tigervnc-common on 20.04)" >&2
        exit 1
    fi
done

# locale
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8

# sshd: no auditd in the container, make pam_loginuid optional to avoid login failures
mkdir -p /run/sshd
sed -i 's/^\(session\s\+\)required\(\s\+pam_loginuid.so\)/\1optional\2/' /etc/pam.d/sshd
