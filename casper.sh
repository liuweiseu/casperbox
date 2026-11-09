#!/usr/bin/env bash
# casper-container 入口脚本：构建镜像、启动/停止容器
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
用法: $(basename "$0") [-c 配置文件] <命令> [版本] [选项]

命令:
  list                 列出支持的 toolflow 版本
  build [版本]         构建镜像（选项 --no-cache）
  up [版本]            启动容器（镜像不存在时自动构建）
  down [版本]          停止并删除容器
  restart [版本]       重启容器（重新读取 user.conf）
  shell [版本]         以容器用户身份进入容器
  status [版本]        查看容器状态
  logs [版本]          查看容器日志
  config [版本]        显示生成的 compose 配置（隐藏密码）

选项:
  -c, --config FILE    指定用户配置文件（默认 config/user.conf，也可用环境变量 CASPER_CONFIG）
  -h, --help           显示帮助

未指定版本时：build/up 只有一个版本则自动选择，否则交互选择；
其它命令使用上一次 build/up 的版本。
EOF
}

# 解析版本、加载并校验配置、生成 compose 文件
prepare() {
    local requested="$1" use_last="$2" ver
    ver="$(resolve_version "${requested}" "${use_last}")"
    verify_version_integrity "${ver}"
    load_user_config
    load_toolflow_conf "${ver}"   # 后加载，保证版本参数不会被 user.conf 覆盖

    HOST_UID="$(id -u)"
    HOST_GID="$(id -g)"
    (( HOST_UID != 0 )) || die "请不要以 root 运行，使用普通用户 + rootless podman"

    validate_user_config
    IMAGE_NAME="casper-toolflow:${TOOLFLOW_VERSION}"
    PROJECT_NAME="casper-${TOOLFLOW_VERSION}"

    generate_env_file
    generate_override_file
    save_state
    log_info "toolflow 版本: ${TOOLFLOW_VERSION}，镜像: ${IMAGE_NAME}，容器: ${CONTAINER_NAME}"
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

# 镜像中的用户必须与当前配置一致，否则挂载目录权限会出错
check_image_matches() {
    local img_user img_uid img_gid
    img_user="$(image_label casper.user)"
    img_uid="$(image_label casper.uid)"
    img_gid="$(image_label casper.gid)"
    if [[ "${img_user}" != "${CONTAINER_USER}" || "${img_uid}" != "${HOST_UID}" || "${img_gid}" != "${HOST_GID}" ]]; then
        die "镜像 ${IMAGE_NAME} 的用户(${img_user} ${img_uid}:${img_gid}) 与当前配置(${CONTAINER_USER} ${HOST_UID}:${HOST_GID}) 不一致，请执行: $0 build ${TOOLFLOW_VERSION}"
    fi
}

cmd_build() {
    local ver="" args=()
    while (( $# )); do
        case "$1" in
            --no-cache) args+=(--no-cache) ;;
            -*) die "未知选项: $1" ;;
            *) ver="$1" ;;
        esac
        shift
    done
    prepare "${ver}" 0
    log_info "开始构建镜像 ${IMAGE_NAME}（首次构建需要下载较多内容，请耐心等待）"
    compose build "${args[@]}" casper
    log_info "镜像构建完成: ${IMAGE_NAME}"
}

print_access_info() {
    local host="localhost"
    [[ "${BIND_ADDRESS}" != "0.0.0.0" ]] && host="${BIND_ADDRESS}"
    cat <<EOF

容器 ${CONTAINER_NAME} 已启动
  SSH : ssh -p ${SSH_PORT} ${CONTAINER_USER}@${host}
  VNC : ${host}:${VNC_PORT}
  进入: $0 shell
EOF
    local s
    for s in "${EXTRA_PORT_SPECS[@]}"; do
        echo "  端口: ${s#"${BIND_ADDRESS}:"}"
    done
}

cmd_up() {
    prepare "${1:-}" 0
    if ! podman image exists "${IMAGE_NAME}"; then
        log_info "镜像 ${IMAGE_NAME} 不存在，开始构建"
        compose build casper
    fi
    check_image_matches
    # 重新创建容器，使 user.conf 的修改生效
    compose up -d --force-recreate
    print_access_info
}

cmd_down() {
    prepare "${1:-}" 1
    compose down
}

cmd_shell() {
    prepare "${1:-}" 1
    podman container exists "${CONTAINER_NAME}" || die "容器 ${CONTAINER_NAME} 不存在，请先执行: $0 up"
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
                [[ -n "${2:-}" ]] || die "$1 需要参数"
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
        *) usage; die "未知命令: ${cmd}" ;;
    esac
}

main "$@"
