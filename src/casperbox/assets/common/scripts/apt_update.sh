#!/usr/bin/env bash
# apt-get update that fails when any package index could not be downloaded, with retries.
#
# apt-get update only warns ("W: Failed to fetch ...") and exits 0 when a mirror is unreachable;
# the next apt-get install then fails with misleading "has no installation candidate" errors.
# Attempts: APT_UPDATE_ATTEMPTS (default 5), waiting 10 s, 20 s, ... between them.
set -uo pipefail

attempts="${APT_UPDATE_ATTEMPTS:-5}"
for (( i = 1; i <= attempts; i++ )); do
    out="$(apt-get update -o Acquire::Retries=3 2>&1)"
    rc=$?
    printf '%s\n' "${out}"
    if (( rc == 0 )) && ! grep -qE '^(W|E): (Failed to fetch|Some index files failed)' <<<"${out}"; then
        exit 0
    fi
    if (( i < attempts )); then
        echo "apt-get update could not download all package indexes (attempt ${i}/${attempts}), retrying in $(( i * 10 )) s" >&2
        sleep $(( i * 10 ))
    fi
done
echo "Error: apt-get update could not download the package indexes after ${attempts} attempts;" \
     "check the network connection or the apt mirror, then run the build again" >&2
exit 1
