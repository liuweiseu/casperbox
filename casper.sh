#!/usr/bin/env bash
# casper-container entry script: build images, start/stop containers
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
GEN_DIR="${ROOT_DIR}/.generated"
STATE_FILE="${GEN_DIR}/state"
USER_CONF="${CASPER_CONFIG:-${ROOT_DIR}/config/user.conf}"

# shellcheck source=lib/common.sh
source "${ROOT_DIR}/lib/common.sh"
# shellcheck source=lib/version.sh
source "${ROOT_DIR}/lib/version.sh"
# shellcheck source=lib/config.sh
source "${ROOT_DIR}/lib/config.sh"
# shellcheck source=lib/compose_gen.sh
source "${ROOT_DIR}/lib/compose_gen.sh"

usage() {
    cat <<EOF
Usage: $(basename "$0") [-c CONFIG] <command> [version] [options]

Commands:
  list                 List supported toolflow versions
  build [version]      Build the image (option: --no-cache)
  up [version]         Start the container (builds the image if missing)
  down [version]       Stop and remove the container
  restart [version]    Restart the container (re-reads user.conf)
  shell [version]      Open a shell in the container as the container user
  status [version]     Show container status
  logs [version]       Follow container logs
  config [version]     Show the generated compose config (passwords masked)

Options:
  -c, --config FILE    User config file (default: config/user.conf, or env CASPER_CONFIG)
  -h, --help           Show this help

When no version is given: build/up pick the only version automatically, or
prompt if there are several; other commands use the version of the last build/up.
EOF
}

# Resolve the version, load and validate config, generate compose files
prepare() {
    local requested="$1" use_last="$2" ver
    ver="$(resolve_version "${requested}" "${use_last}")"
    verify_version_integrity "${ver}"
    load_user_config
    load_toolflow_conf "${ver}"   # loaded last so user.conf cannot override version settings

    HOST_UID="$(id -u)"
    HOST_GID="$(id -g)"
    (( HOST_UID != 0 )) || die "Do not run as root; use a regular user with rootless podman"

    validate_user_config
    IMAGE_NAME="casper-toolflow:${TOOLFLOW_VERSION}"
    PROJECT_NAME="casper-${TOOLFLOW_VERSION}"

    generate_env_file
    generate_override_file
    save_state
    log_info "toolflow version: ${TOOLFLOW_VERSION}, image: ${IMAGE_NAME}, container: ${CONTAINER_NAME}"
}

compose() {
    podman compose \
        --env-file "${GEN_DIR}/.env" \
        -p "${PROJECT_NAME}" \
        -f "${ROOT_DIR}/compose.yml" \
        -f "${GEN_DIR}/compose.override.yml" \
        "$@"
}

image_label() {
    podman image inspect --format "{{index .Labels \"$1\"}}" "${IMAGE_NAME}" 2>/dev/null || true
}

# The image user must match the current config, otherwise mounted directories get wrong permissions
check_image_matches() {
    local img_user img_uid img_gid
    img_user="$(image_label casper.user)"
    img_uid="$(image_label casper.uid)"
    img_gid="$(image_label casper.gid)"
    if [[ "${img_user}" != "${CONTAINER_USER}" || "${img_uid}" != "${HOST_UID}" || "${img_gid}" != "${HOST_GID}" ]]; then
        die "User in image ${IMAGE_NAME} (${img_user} ${img_uid}:${img_gid}) does not match the current config (${CONTAINER_USER} ${HOST_UID}:${HOST_GID}); please run: $0 build ${TOOLFLOW_VERSION}"
    fi
}

cmd_build() {
    local ver="" args=()
    while (( $# )); do
        case "$1" in
            --no-cache) args+=(--no-cache) ;;
            -*) die "Unknown option: $1" ;;
            *) ver="$1" ;;
        esac
        shift
    done
    prepare "${ver}" 0
    log_info "Building image ${IMAGE_NAME} (the first build downloads a lot, please be patient)"
    compose build "${args[@]}" casper
    log_info "Image built: ${IMAGE_NAME}"
}

print_access_info() {
    local host="localhost"
    [[ "${BIND_ADDRESS}" != "0.0.0.0" ]] && host="${BIND_ADDRESS}"
    cat <<EOF

Container ${CONTAINER_NAME} is up
  SSH  : ssh -p ${SSH_PORT} ${CONTAINER_USER}@${host}
  VNC  : ${host}:${VNC_PORT}
  Shell: $0 shell
EOF
    local s
    for s in "${EXTRA_PORT_SPECS[@]}"; do
        echo "  Port : ${s#"${BIND_ADDRESS}:"}"
    done
}

cmd_up() {
    prepare "${1:-}" 0
    if ! podman image exists "${IMAGE_NAME}"; then
        log_info "Image ${IMAGE_NAME} not found, building it"
        compose build casper
    fi
    check_image_matches
    # Recreate the container so that changes in user.conf take effect
    compose up -d --force-recreate
    print_access_info
}

cmd_down() {
    prepare "${1:-}" 1
    compose down
}

cmd_shell() {
    prepare "${1:-}" 1
    podman container exists "${CONTAINER_NAME}" || die "Container ${CONTAINER_NAME} does not exist, please run: $0 up"
    exec podman exec -it -u "${CONTAINER_USER}" -w "/home/${CONTAINER_USER}" "${CONTAINER_NAME}" bash -l
}

cmd_status() {
    prepare "${1:-}" 1
    podman ps -a --filter "name=^${CONTAINER_NAME}\$" \
        --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
}

cmd_logs() {
    prepare "${1:-}" 1
    exec podman logs -f "${CONTAINER_NAME}"
}

cmd_config() {
    prepare "${1:-}" 1
    compose config | sed -E 's/((CONTAINER_PWD|VNC_PASSWORD):[[:space:]]*).*/\1******/'
}

main() {
    while (( $# )); do
        case "$1" in
            -c|--config)
                [[ -n "${2:-}" ]] || die "$1 requires an argument"
                USER_CONF="$(realpath -m -- "$2")"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) break ;;
        esac
    done

    local cmd="${1:-}"
    [[ -n "${cmd}" ]] || { usage; exit 1; }
    shift

    case "${cmd}" in
        list) list_versions; return ;;
        help) usage; return ;;
    esac

    check_dependencies
    case "${cmd}" in
        build)   cmd_build "$@" ;;
        up)      cmd_up "$@" ;;
        down)    cmd_down "$@" ;;
        restart) cmd_down "$@"; cmd_up "$@" ;;
        shell)   cmd_shell "$@" ;;
        status)  cmd_status "$@" ;;
        logs)    cmd_logs "$@" ;;
        config)  cmd_config "$@" ;;
        *) usage; die "Unknown command: ${cmd}" ;;
    esac
}

main "$@"
