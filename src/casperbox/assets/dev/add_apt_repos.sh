#!/usr/bin/env bash
# Add the third-party apt repositories of [[apt_repos]] in casperbox-base.toml.
#
#   APT_REPOS: space-separated entries "name|key|url|suite|components|fingerprint", written by
#              casperbox (AptRepo.build_arg); suite empty = the OS codename, ending in "/" = flat
#              repository; components comma-separated; fingerprint optional (40 hex digits).
#   APT_ROOT:  prefix for /etc (tests only)
#
# For each repository: download the signing key to /etc/apt/keyrings/<name>.asc (ASCII-armored)
# or <name>.gpg (binary), check its fingerprint if one is given, and write
# /etc/apt/sources.list.d/<name>.sources. The packages are installed by install_packages.sh.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

read -r -a repos <<< "${APT_REPOS:-}"
if (( ${#repos[@]} == 0 )); then
    echo "No extra apt repositories"
    exit 0
fi

root="${APT_ROOT:-}"
keyrings="${root}/etc/apt/keyrings"
sources="${root}/etc/apt/sources.list.d"
install -d -m 0755 "${keyrings}" "${sources}"
# shellcheck disable=SC1091
codename="$(. "${root}/etc/os-release" && echo "${VERSION_CODENAME:-}")"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# gpg is only needed to check fingerprints; the base image does not have it
ensure_gpg() {
    command -v gpg >/dev/null && return
    echo "Installing gnupg to check key fingerprints"
    bash "$(dirname "$0")/apt_update.sh"
    apt-get install -y --no-install-recommends gnupg
}

# Fingerprints of the primary keys in a key file, one per line
key_fingerprints() {
    GNUPGHOME="${tmp}/gnupg" gpg --batch --show-keys --with-colons "$1" 2>/dev/null \
        | awk -F: '$1 == "pub" { primary = 1; next } $1 == "sub" { primary = 0 } $1 == "fpr" && primary { print $10; primary = 0 }'
}

for entry in "${repos[@]}"; do
    IFS='|' read -r name key url suite components fingerprint <<< "${entry}"
    echo "==> apt repository ${name}: ${url}"

    if ! curl -fsSL --retry 3 -o "${tmp}/key" "${key}"; then
        echo "Error: apt repository ${name}: cannot download the signing key ${key}" >&2
        exit 1
    fi
    if grep -q -- "-----BEGIN PGP PUBLIC KEY BLOCK-----" "${tmp}/key"; then
        keyfile="/etc/apt/keyrings/${name}.asc"
    else
        keyfile="/etc/apt/keyrings/${name}.gpg"
    fi

    if [[ -n "${fingerprint}" ]]; then
        ensure_gpg
        install -d -m 0700 "${tmp}/gnupg"
        found="$(key_fingerprints "${tmp}/key")"
        if [[ -z "${found}" ]]; then
            echo "Error: apt repository ${name}: ${key} is not an OpenPGP key" >&2
            exit 1
        fi
        # Every key in the file must be the expected one: apt trusts all keys of a keyring
        if [[ "$(sort -u <<< "${found}")" != "${fingerprint}" ]]; then
            echo "Error: apt repository ${name}: fingerprint mismatch for ${key}" >&2
            echo "  expected: ${fingerprint}" >&2
            echo "  found:    $(tr '\n' ' ' <<< "${found}")" >&2
            exit 1
        fi
        echo "    key fingerprint ${fingerprint} OK"
    fi

    install -m 0644 "${tmp}/key" "${root}${keyfile}"

    suite="${suite:-${codename}}"
    if [[ -z "${suite}" ]]; then
        echo "Error: apt repository ${name}: no suite given and the OS codename is unknown" >&2
        exit 1
    fi
    {
        echo "# [[apt_repos]] ${name} in casperbox-base.toml"
        echo "Types: deb"
        echo "URIs: ${url}"
        echo "Suites: ${suite}"
        [[ "${suite}" == */ ]] || echo "Components: ${components//,/ }"
        echo "Signed-By: ${keyfile}"
    } > "${sources}/${name}.sources"
    chmod 0644 "${sources}/${name}.sources"
    sed 's/^/    /' "${sources}/${name}.sources"
done
