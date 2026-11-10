#!/usr/bin/env bash
# Point the Ubuntu apt sources at the mirror in APT_MIRROR ([build] apt_mirror in the config).
# Replaces archive.ubuntu.com (also country mirrors such as us.archive.ubuntu.com) and
# security.ubuntu.com in both formats:
#   /etc/apt/sources.list                    Ubuntu 20.04 / 22.04
#   /etc/apt/sources.list.d/ubuntu.sources   Ubuntu 24.04+ (deb822)
# Empty APT_MIRROR: keep the base image's sources. The mirror stays configured in the image.
set -euo pipefail

mirror="${APT_MIRROR:-}"
if [[ -z "${mirror}" ]]; then
    echo "apt mirror: default (from the base image)"
    exit 0
fi
mirror="${mirror%/}"

files=()
for f in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
    [[ -f "${f}" ]] && files+=("${f}")
done
pattern='https?://([a-z]{2}\.)?(archive|security)\.ubuntu\.com/ubuntu/?'
if (( ${#files[@]} == 0 )) || ! grep -qE "${pattern}" "${files[@]}"; then
    echo "Error: apt_mirror is set, but no Ubuntu apt sources (archive/security.ubuntu.com) were found" \
         "to replace; apt_mirror only works with Ubuntu base images" >&2
    exit 1
fi

sed -i -E "s#${pattern}#${mirror}/#g" "${files[@]}"
echo "apt mirror: ${mirror}"
grep -hE '^[[:space:]]*(deb|URIs:)' "${files[@]}" | sed 's/^/    /'
