# shellcheck shell=bash
# 用户配置：加载 config/user.conf、填充默认值、校验

USER_CONF_EXAMPLE="${ROOT_DIR}/config/user.conf.example"

load_user_config() {
    local conf="${USER_CONF}"
    if [[ ! -f "${conf}" ]]; then
        if [[ "${conf}" == "${ROOT_DIR}/config/user.conf" ]]; then
            cp "${USER_CONF_EXAMPLE}" "${conf}"
            die "已根据模板生成 ${conf}，请修改其中的目录和端口后重新运行"
        fi
        die "配置文件不存在: ${conf}"
    fi

    # 默认值
    VIVADO_MOUNT_OPTS=ro
    BIND_ADDRESS=0.0.0.0
    CONTAINER_USER=casper
    CONTAINER_PWD=casper
    VNC_PASSWORD=casper
    CONTAINER_NAME=
    CONTAINER_MAC=
    VNC_GEOMETRY=1920x1080
    SHM_SIZE=2g
    TZ=UTC
    EXTRA_MOUNTS=()
    EXTRA_PORTS=()

    # shellcheck disable=SC1090
    source "${conf}"
}

# 拒绝会破坏 .env 的字符
_check_env_value() {
    local name="$1" value="$2"
    [[ "${value}" != *"'"* && "${value}" != *$'\n'* ]] \
        || die "${name} 不能包含单引号或换行符"
}

# 必须在 load_toolflow_conf 之后调用（需要 VIVADO_VERSION / TOOLFLOW_VERSION）
validate_user_config() {
    local k
    for k in VIVADO_DIR XIL_LICENSE_DIR WORK_DIR SSH_PORT VNC_PORT CONTAINER_USER CONTAINER_PWD; do
        [[ -n "${!k:-}" ]] || die "user.conf 中必须设置 ${k}"
    done

    # ---- 用户与密码 ----
    [[ "${CONTAINER_USER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] \
        || die "CONTAINER_USER 不合法: ${CONTAINER_USER}"
    [[ "${CONTAINER_USER}" != root ]] || die "CONTAINER_USER 不能是 root"
    VNC_PASSWORD="${VNC_PASSWORD:-${CONTAINER_PWD}}"
    (( ${#VNC_PASSWORD} >= 6 )) || die "VNC_PASSWORD 至少需要 6 位"
    (( ${#VNC_PASSWORD} <= 8 )) || log_warn "VNC_PASSWORD 超过 8 位，只有前 8 位有效"
    [[ "${VNC_GEOMETRY}" =~ ^[0-9]+x[0-9]+$ ]] || die "VNC_GEOMETRY 格式应为 宽x高，例如 1920x1080"
    [[ "${SHM_SIZE}" =~ ^[0-9]+[bkmgBKMG]?$ ]] || die "SHM_SIZE 格式错误: ${SHM_SIZE}"

    # ---- 必需目录 ----
    VIVADO_DIR="$(expand_path "${VIVADO_DIR}")"
    XIL_LICENSE_DIR="$(expand_path "${XIL_LICENSE_DIR}")"
    WORK_DIR="$(expand_path "${WORK_DIR}")"

    [[ -d "${VIVADO_DIR}" ]] || die "VIVADO_DIR 不存在: ${VIVADO_DIR}"
    if [[ ! -f "${VIVADO_DIR}/Vivado/${VIVADO_VERSION}/settings64.sh" && ! -f "${VIVADO_DIR}/settings64.sh" ]]; then
        log_warn "在 ${VIVADO_DIR} 下未找到 Vivado/${VIVADO_VERSION}/settings64.sh 或 settings64.sh，toolflow ${TOOLFLOW_VERSION} 需要 Vivado ${VIVADO_VERSION}"
    fi
    # Vivado 挂载到容器内相同路径，不能覆盖容器的系统目录或 toolflow 目录
    local protected
    for protected in /bin /boot /dev /etc /lib /lib64 /proc /root /run /sbin /sys /tmp /usr /var \
            /opt/conda /opt/scilab /opt/casper "/home/${CONTAINER_USER}" \
            "/home/${CONTAINER_USER}/work" "/home/${CONTAINER_USER}/.Xilinx" \
            "/home/${CONTAINER_USER}/toolflow" "/home/${CONTAINER_USER}/.local"; do
        if [[ "${protected}" == "${VIVADO_DIR}" || "${protected}" == "${VIVADO_DIR}/"* ]]; then
            die "VIVADO_DIR=${VIVADO_DIR} 会覆盖容器内的 ${protected}，请使用其它路径"
        fi
    done
    [[ "${VIVADO_MOUNT_OPTS}" == ro || "${VIVADO_MOUNT_OPTS}" == rw ]] \
        || die "VIVADO_MOUNT_OPTS 只能是 ro 或 rw"

    [[ -d "${XIL_LICENSE_DIR}" ]] || die "XIL_LICENSE_DIR 不存在: ${XIL_LICENSE_DIR}"

    if [[ ! -d "${WORK_DIR}" ]]; then
        log_info "创建工作目录 ${WORK_DIR}"
        mkdir -p "${WORK_DIR}"
    fi

    # ---- 端口 ----
    local -A used_host_ports=()
    local p
    for p in SSH_PORT VNC_PORT; do
        is_port "${!p}" || die "${p} 不是合法端口: ${!p}"
        [[ -z "${used_host_ports[${!p}/tcp]:-}" ]] || die "宿主机端口 ${!p} 重复"
        used_host_ports[${!p}/tcp]=1
    done

    # ---- 自定义端口 ----
    EXTRA_PORT_SPECS=()
    local spec host_port cport proto
    for spec in "${EXTRA_PORTS[@]}"; do
        [[ "${spec}" =~ ^([0-9]+):([0-9]+)(/(tcp|udp))?$ ]] \
            || die "EXTRA_PORTS 格式错误: '${spec}'（应为 宿主机端口:容器端口[/tcp|/udp]）"
        host_port="${BASH_REMATCH[1]}"; cport="${BASH_REMATCH[2]}"; proto="${BASH_REMATCH[4]:-tcp}"
        if ! is_port "${host_port}" || ! is_port "${cport}"; then
            die "EXTRA_PORTS 端口超出范围: '${spec}'"
        fi
        [[ "${cport}" != 22 && "${cport}" != 5901 ]] || die "EXTRA_PORTS 不能使用容器端口 22/5901: '${spec}'"
        [[ -z "${used_host_ports[${host_port}/${proto}]:-}" ]] \
            || die "宿主机端口 ${host_port}/${proto} 重复"
        used_host_ports[${host_port}/${proto}]=1
        EXTRA_PORT_SPECS+=("${BIND_ADDRESS}:${host_port}:${cport}/${proto}")
    done

    # ---- 自定义挂载 ----
    local home="/home/${CONTAINER_USER}"
    local -A reserved=(
        ["${VIVADO_DIR}"]=1 ["${home}/.Xilinx"]=1 ["${home}/work"]=1
        ["${home}"]=1 ["${home}/toolflow"]=1
    )
    EXTRA_MOUNT_SPECS=()
    local hpath cpath opts rest
    for spec in "${EXTRA_MOUNTS[@]}"; do
        IFS=':' read -r hpath cpath opts rest <<<"${spec}"
        [[ -n "${hpath}" && -n "${cpath}" && -z "${rest}" ]] \
            || die "EXTRA_MOUNTS 格式错误: '${spec}'（应为 宿主机路径:容器路径[:ro|rw]）"
        opts="${opts:-rw}"
        [[ "${opts}" == ro || "${opts}" == rw ]] || die "EXTRA_MOUNTS 选项只能是 ro 或 rw: '${spec}'"

        hpath="$(expand_path "${hpath}")"
        [[ -e "${hpath}" ]] || die "EXTRA_MOUNTS 宿主机路径不存在: ${hpath}"

        # shellcheck disable=SC2088  # 这里匹配的是字面量 ~
        case "${cpath}" in
            "~")   cpath="${home}" ;;
            "~/"*) cpath="${home}/${cpath#"~/"}" ;;
        esac
        [[ "${cpath}" == /* ]] || die "EXTRA_MOUNTS 容器路径必须是绝对路径或以 ~/ 开头: '${spec}'"
        cpath="$(realpath -m -- "${cpath}")"
        [[ -z "${reserved[${cpath}]:-}" ]] || die "EXTRA_MOUNTS 容器路径与保留路径冲突: ${cpath}"
        reserved[${cpath}]=1

        EXTRA_MOUNT_SPECS+=("${hpath}:${cpath}:${opts}")
    done

    # ---- MAC 地址（可选）----
    if [[ -n "${CONTAINER_MAC}" ]]; then
        CONTAINER_MAC="${CONTAINER_MAC,,}"
        CONTAINER_MAC="${CONTAINER_MAC//-/:}"
        [[ "${CONTAINER_MAC}" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] \
            || die "CONTAINER_MAC 格式错误: ${CONTAINER_MAC}（应为 xx:xx:xx:xx:xx:xx）"
        # 第一个字节最低位为 1 表示组播地址，不能用作网卡地址
        (( (16#${CONTAINER_MAC:0:2} & 1) == 0 )) \
            || die "CONTAINER_MAC 不能是组播地址: ${CONTAINER_MAC}"
        [[ "${CONTAINER_MAC}" != "00:00:00:00:00:00" ]] || die "CONTAINER_MAC 不能全为 0"
    fi

    # ---- pasta 网络参数 ----
    # 容器内网卡固定命名为 eth0（pasta 默认沿用宿主机网卡名）
    # shellcheck disable=SC2034  # 由 compose_gen.sh 写入 .env
    PASTA_NETWORK="pasta:-I,eth0"
    if [[ -n "${CONTAINER_MAC}" ]]; then
        PASTA_NETWORK+=",--ns-mac-addr,${CONTAINER_MAC}"
    fi

    # ---- 其它 ----
    CONTAINER_NAME="${CONTAINER_NAME:-casper-${TOOLFLOW_VERSION}}"
    [[ "${CONTAINER_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "CONTAINER_NAME 不合法: ${CONTAINER_NAME}"
    # shellcheck disable=SC2034  # 由 compose_gen.sh 写入 .env
    CONTAINER_HOSTNAME="casper-${TOOLFLOW_VERSION}"

    for k in VIVADO_DIR XIL_LICENSE_DIR WORK_DIR CONTAINER_PWD VNC_PASSWORD TZ; do
        _check_env_value "${k}" "${!k}"
    done
}
