# shellcheck shell=bash
# 公共函数：日志、错误处理、依赖检查、路径处理

if [[ -t 2 ]]; then
    _C_RED=$'\033[31m'; _C_GREEN=$'\033[32m'; _C_YELLOW=$'\033[33m'; _C_RESET=$'\033[0m'
else
    _C_RED=; _C_GREEN=; _C_YELLOW=; _C_RESET=
fi

log_info()  { printf '%s[INFO]%s  %s\n' "${_C_GREEN}" "${_C_RESET}" "$*" >&2; }
log_warn()  { printf '%s[WARN]%s  %s\n' "${_C_YELLOW}" "${_C_RESET}" "$*" >&2; }
log_error() { printf '%s[ERROR]%s %s\n' "${_C_RED}" "${_C_RESET}" "$*" >&2; }
die()       { log_error "$*"; exit 1; }

# 检查 podman 与 podman-compose，并确保 podman compose 使用 podman-compose
check_dependencies() {
    command -v podman >/dev/null 2>&1 || die "未找到 podman，请先安装 podman"
    command -v podman-compose >/dev/null 2>&1 \
        || die "未找到 podman-compose（需要 >= 1.1.0），可通过 pip install podman-compose 或发行版包管理器安装"

    command -v pasta >/dev/null 2>&1 \
        || die "未找到 pasta，请安装 passt 软件包（容器使用 pasta 网络模式，需要 podman >= 4.4）"

    # docker-compose 不支持 userns keep-id，强制使用 podman-compose
    export PODMAN_COMPOSE_PROVIDER=podman-compose
    export PODMAN_COMPOSE_WARNING_LOGS=false

    local rootless
    rootless="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)"
    [[ "${rootless}" == "true" ]] \
        || die "请以普通用户身份运行 rootless podman（当前不是 rootless 模式）"
}

# 展开 ~ 并转为绝对路径；相对路径相对于仓库根目录
expand_path() {
    local p="$1"
    # shellcheck disable=SC2088  # 这里匹配的是字面量 ~
    case "${p}" in
        "~")   p="${HOME}" ;;
        "~/"*) p="${HOME}/${p#"~/"}" ;;
    esac
    (cd "${ROOT_DIR}" && realpath -m -- "${p}")
}

is_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}
