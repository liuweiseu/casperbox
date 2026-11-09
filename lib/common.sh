# shellcheck shell=bash
# Common helpers: logging, error handling, dependency checks, path handling

if [[ -t 2 ]]; then
    _C_RED=$'\033[31m'; _C_GREEN=$'\033[32m'; _C_YELLOW=$'\033[33m'; _C_RESET=$'\033[0m'
else
    _C_RED=; _C_GREEN=; _C_YELLOW=; _C_RESET=
fi

log_info()  { printf '%s[INFO]%s  %s\n' "${_C_GREEN}" "${_C_RESET}" "$*" >&2; }
log_warn()  { printf '%s[WARN]%s  %s\n' "${_C_YELLOW}" "${_C_RESET}" "$*" >&2; }
log_error() { printf '%s[ERROR]%s %s\n' "${_C_RED}" "${_C_RESET}" "$*" >&2; }
die()       { log_error "$*"; exit 1; }

# Check podman and podman-compose, and make `podman compose` use podman-compose
check_dependencies() {
    command -v podman >/dev/null 2>&1 || die "podman not found, please install podman"
    command -v podman-compose >/dev/null 2>&1 \
        || die "podman-compose (>= 1.1.0) not found; install it with pip install podman-compose or your distribution's package manager"

    command -v pasta >/dev/null 2>&1 \
        || die "pasta not found; please install the passt package (the container uses pasta networking, requires podman >= 4.4)"

    # docker-compose does not support userns keep-id, so force podman-compose
    export PODMAN_COMPOSE_PROVIDER=podman-compose
    export PODMAN_COMPOSE_WARNING_LOGS=false

    local rootless
    rootless="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)"
    [[ "${rootless}" == "true" ]] \
        || die "Please run rootless podman as a regular user (podman is not in rootless mode)"
}

# Expand ~ and convert to an absolute path; relative paths are relative to the repo root
expand_path() {
    local p="$1"
    # shellcheck disable=SC2088  # matching a literal ~ here
    case "${p}" in
        "~")   p="${HOME}" ;;
        "~/"*) p="${HOME}/${p#"~/"}" ;;
    esac
    (cd "${ROOT_DIR}" && realpath -m -- "${p}")
}

is_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}
