# shellcheck shell=bash
# toolflow 版本：列出、选择、完整性校验、加载 toolflow.conf

VERSIONS_DIR="${ROOT_DIR}/versions"

# toolflow.conf 中必须定义的变量
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

# 解析版本：参数 > 上次使用的版本(仅 use_last=1 时) > 唯一版本 > 交互选择
resolve_version() {
    local requested="$1" use_last="${2:-0}"
    local -a versions
    mapfile -t versions < <(list_versions)
    (( ${#versions[@]} > 0 )) || die "versions/ 下没有可用的 toolflow 版本"

    if [[ -n "${requested}" ]]; then
        local v
        for v in "${versions[@]}"; do
            [[ "${v}" == "${requested}" ]] && { echo "${v}"; return; }
        done
        die "不支持的版本: ${requested}（可用: ${versions[*]}）"
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

    [[ -t 0 ]] || die "存在多个版本，请在命令行指定版本（可用: ${versions[*]}）"
    local choice
    PS3="请选择 toolflow 版本: "
    select choice in "${versions[@]}"; do
        [[ -n "${choice}" ]] && { echo "${choice}"; return; }
    done
    die "未选择版本"
}

# 校验 versions/<ver>/ 下受保护文件未被修改
verify_version_integrity() {
    local ver="$1" sums="${VERSIONS_DIR}/SHA256SUMS"
    [[ -f "${sums}" ]] || die "缺少 ${sums}"
    local entries
    entries="$(grep -E "^[0-9a-f]{64}  ${ver}/" "${sums}" || true)"
    [[ -n "${entries}" ]] || die "SHA256SUMS 中没有版本 ${ver} 的校验记录"
    if ! (cd "${VERSIONS_DIR}" && sha256sum --quiet -c - <<<"${entries}" >/dev/null 2>&1); then
        die "versions/${ver} 下的文件已被修改（校验和不匹配）。请恢复原文件；用户配置请修改 config/user.conf"
    fi
}

load_toolflow_conf() {
    local ver="$1" conf="${VERSIONS_DIR}/$1/toolflow.conf"
    # shellcheck disable=SC1090
    source "${conf}"
    local k
    for k in "${TOOLFLOW_KEYS[@]}"; do
        [[ -n "${!k:-}" ]] || die "${conf} 缺少 ${k}"
    done
    [[ "${TOOLFLOW_VERSION}" == "${ver}" ]] \
        || die "${conf} 中 TOOLFLOW_VERSION=${TOOLFLOW_VERSION} 与目录名 ${ver} 不一致"
}
