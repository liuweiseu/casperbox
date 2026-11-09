# shellcheck shell=bash
# toolflow versions: list, select, verify integrity, load toolflow.conf

VERSIONS_DIR="${ROOT_DIR}/versions"

# Variables that toolflow.conf must define
TOOLFLOW_KEYS=(
    TOOLFLOW_VERSION BASE_IMAGE VIVADO_VERSION
    MINICONDA_INSTALLER PYTHON_VERSION CONDA_ENV_NAME
    MLIB_DEVEL_REPO MLIB_DEVEL_BRANCH
    DEVICE_TREE_XLNX_REPO DEVICE_TREE_XLNX_BRANCH GIT_CLONE_DEPTH
    SCILAB_VERSION SCILAB_URL
)

list_versions() {
    local d
    for d in "${VERSIONS_DIR}"/*/; do
        d="${d%/}"
        [[ -f "${d}/Dockerfile" && -f "${d}/toolflow.conf" ]] && basename "${d}"
    done
}

# Resolve version: argument > last used version (only if use_last=1) > the only version > interactive choice
resolve_version() {
    local requested="$1" use_last="${2:-0}"
    local -a versions
    mapfile -t versions < <(list_versions)
    (( ${#versions[@]} > 0 )) || die "No toolflow version available under versions/"

    if [[ -n "${requested}" ]]; then
        local v
        for v in "${versions[@]}"; do
            [[ "${v}" == "${requested}" ]] && { echo "${v}"; return; }
        done
        die "Unsupported version: ${requested} (available: ${versions[*]})"
    fi

    if [[ "${use_last}" == 1 && -f "${STATE_FILE}" ]]; then
        local last
        last="$(sed -n 's/^CURRENT_VERSION=//p' "${STATE_FILE}")"
        if [[ -n "${last}" && -d "${VERSIONS_DIR}/${last}" ]]; then
            echo "${last}"; return
        fi
    fi

    if (( ${#versions[@]} == 1 )); then
        echo "${versions[0]}"; return
    fi

    [[ -t 0 ]] || die "Multiple versions available, please specify one on the command line (available: ${versions[*]})"
    local choice
    PS3="Select a toolflow version: "
    select choice in "${versions[@]}"; do
        [[ -n "${choice}" ]] && { echo "${choice}"; return; }
    done
    die "No version selected"
}

# Verify that the protected files under versions/<ver>/ have not been modified
verify_version_integrity() {
    local ver="$1" sums="${VERSIONS_DIR}/SHA256SUMS"
    [[ -f "${sums}" ]] || die "Missing ${sums}"
    local entries
    entries="$(grep -E "^[0-9a-f]{64}  ${ver}/" "${sums}" || true)"
    [[ -n "${entries}" ]] || die "SHA256SUMS has no checksums for version ${ver}"
    if ! (cd "${VERSIONS_DIR}" && sha256sum --quiet -c - <<<"${entries}" >/dev/null 2>&1); then
        die "Files under versions/${ver} have been modified (checksum mismatch). Please restore them; put user settings in config/user.conf"
    fi
}

load_toolflow_conf() {
    local ver="$1" conf="${VERSIONS_DIR}/$1/toolflow.conf"
    # shellcheck disable=SC1090
    source "${conf}"
    local k
    for k in "${TOOLFLOW_KEYS[@]}"; do
        [[ -n "${!k:-}" ]] || die "${conf} is missing ${k}"
    done
    [[ "${TOOLFLOW_VERSION}" == "${ver}" ]] \
        || die "TOOLFLOW_VERSION=${TOOLFLOW_VERSION} in ${conf} does not match the directory name ${ver}"
}
