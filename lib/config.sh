# shellcheck shell=bash
# User config: load config/user.conf, apply defaults, validate

USER_CONF_EXAMPLE="${ROOT_DIR}/config/user.conf.example"

load_user_config() {
    local conf="${USER_CONF}"
    if [[ ! -f "${conf}" ]]; then
        if [[ "${conf}" == "${ROOT_DIR}/config/user.conf" ]]; then
            cp "${USER_CONF_EXAMPLE}" "${conf}"
            die "Created ${conf} from the template; please edit its directories and ports and run again"
        fi
        die "Config file not found: ${conf}"
    fi

    # Defaults
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

# Reject characters that would break .env
_check_env_value() {
    local name="$1" value="$2"
    [[ "${value}" != *"'"* && "${value}" != *$'\n'* ]] \
        || die "${name} must not contain single quotes or newlines"
}

# Must be called after load_toolflow_conf (needs VIVADO_VERSION / TOOLFLOW_VERSION)
validate_user_config() {
    local k
    for k in VIVADO_DIR XIL_LICENSE_DIR WORK_DIR SSH_PORT VNC_PORT CONTAINER_USER CONTAINER_PWD; do
        [[ -n "${!k:-}" ]] || die "${k} must be set in user.conf"
    done

    # ---- User and passwords ----
    [[ "${CONTAINER_USER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] \
        || die "Invalid CONTAINER_USER: ${CONTAINER_USER}"
    [[ "${CONTAINER_USER}" != root ]] || die "CONTAINER_USER must not be root"
    VNC_PASSWORD="${VNC_PASSWORD:-${CONTAINER_PWD}}"
    (( ${#VNC_PASSWORD} >= 6 )) || die "VNC_PASSWORD must be at least 6 characters"
    (( ${#VNC_PASSWORD} <= 8 )) || log_warn "VNC_PASSWORD is longer than 8 characters; only the first 8 are used"
    [[ "${VNC_GEOMETRY}" =~ ^[0-9]+x[0-9]+$ ]] || die "VNC_GEOMETRY must be WIDTHxHEIGHT, e.g. 1920x1080"
    [[ "${SHM_SIZE}" =~ ^[0-9]+[bkmgBKMG]?$ ]] || die "Invalid SHM_SIZE: ${SHM_SIZE}"

    # ---- Required directories ----
    VIVADO_DIR="$(expand_path "${VIVADO_DIR}")"
    XIL_LICENSE_DIR="$(expand_path "${XIL_LICENSE_DIR}")"
    WORK_DIR="$(expand_path "${WORK_DIR}")"

    [[ -d "${VIVADO_DIR}" ]] || die "VIVADO_DIR does not exist: ${VIVADO_DIR}"
    if [[ ! -f "${VIVADO_DIR}/Vivado/${VIVADO_VERSION}/settings64.sh" && ! -f "${VIVADO_DIR}/settings64.sh" ]]; then
        log_warn "Neither Vivado/${VIVADO_VERSION}/settings64.sh nor settings64.sh found under ${VIVADO_DIR}; toolflow ${TOOLFLOW_VERSION} requires Vivado ${VIVADO_VERSION}"
    fi
    # Vivado is mounted at the same path in the container; it must not shadow system or toolflow directories
    local protected
    for protected in /bin /boot /dev /etc /lib /lib64 /proc /root /run /sbin /sys /tmp /usr /var \
            /opt/conda /opt/scilab /opt/casper "/home/${CONTAINER_USER}" \
            "/home/${CONTAINER_USER}/work" "/home/${CONTAINER_USER}/.Xilinx" \
            "/home/${CONTAINER_USER}/toolflow" "/home/${CONTAINER_USER}/.local"; do
        if [[ "${protected}" == "${VIVADO_DIR}" || "${protected}" == "${VIVADO_DIR}/"* ]]; then
            die "VIVADO_DIR=${VIVADO_DIR} would shadow ${protected} in the container, please use another path"
        fi
    done
    [[ "${VIVADO_MOUNT_OPTS}" == ro || "${VIVADO_MOUNT_OPTS}" == rw ]] \
        || die "VIVADO_MOUNT_OPTS must be ro or rw"

    [[ -d "${XIL_LICENSE_DIR}" ]] || die "XIL_LICENSE_DIR does not exist: ${XIL_LICENSE_DIR}"

    if [[ ! -d "${WORK_DIR}" ]]; then
        log_info "Creating work directory ${WORK_DIR}"
        mkdir -p "${WORK_DIR}"
    fi

    # ---- Ports ----
    local -A used_host_ports=()
    local p
    for p in SSH_PORT VNC_PORT; do
        is_port "${!p}" || die "${p} is not a valid port: ${!p}"
        [[ -z "${used_host_ports[${!p}/tcp]:-}" ]] || die "Duplicate host port ${!p}"
        used_host_ports[${!p}/tcp]=1
    done

    # ---- Extra ports ----
    EXTRA_PORT_SPECS=()
    local spec host_port cport proto
    for spec in "${EXTRA_PORTS[@]}"; do
        [[ "${spec}" =~ ^([0-9]+):([0-9]+)(/(tcp|udp))?$ ]] \
            || die "Invalid EXTRA_PORTS entry: '${spec}' (expected host_port:container_port[/tcp|/udp])"
        host_port="${BASH_REMATCH[1]}"; cport="${BASH_REMATCH[2]}"; proto="${BASH_REMATCH[4]:-tcp}"
        if ! is_port "${host_port}" || ! is_port "${cport}"; then
            die "EXTRA_PORTS port out of range: '${spec}'"
        fi
        [[ "${cport}" != 22 && "${cport}" != 5901 ]] || die "EXTRA_PORTS must not use container ports 22/5901: '${spec}'"
        [[ -z "${used_host_ports[${host_port}/${proto}]:-}" ]] \
            || die "Duplicate host port ${host_port}/${proto}"
        used_host_ports[${host_port}/${proto}]=1
        EXTRA_PORT_SPECS+=("${BIND_ADDRESS}:${host_port}:${cport}/${proto}")
    done

    # ---- Extra mounts ----
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
            || die "Invalid EXTRA_MOUNTS entry: '${spec}' (expected host_path:container_path[:ro|rw])"
        opts="${opts:-rw}"
        [[ "${opts}" == ro || "${opts}" == rw ]] || die "EXTRA_MOUNTS option must be ro or rw: '${spec}'"

        hpath="$(expand_path "${hpath}")"
        [[ -e "${hpath}" ]] || die "EXTRA_MOUNTS host path does not exist: ${hpath}"

        # shellcheck disable=SC2088  # matching a literal ~ here
        case "${cpath}" in
            "~")   cpath="${home}" ;;
            "~/"*) cpath="${home}/${cpath#"~/"}" ;;
        esac
        [[ "${cpath}" == /* ]] || die "EXTRA_MOUNTS container path must be absolute or start with ~/: '${spec}'"
        cpath="$(realpath -m -- "${cpath}")"
        [[ -z "${reserved[${cpath}]:-}" ]] || die "EXTRA_MOUNTS container path conflicts with a reserved path: ${cpath}"
        reserved[${cpath}]=1

        EXTRA_MOUNT_SPECS+=("${hpath}:${cpath}:${opts}")
    done

    # ---- MAC address (optional) ----
    if [[ -n "${CONTAINER_MAC}" ]]; then
        CONTAINER_MAC="${CONTAINER_MAC,,}"
        CONTAINER_MAC="${CONTAINER_MAC//-/:}"
        [[ "${CONTAINER_MAC}" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]] \
            || die "Invalid CONTAINER_MAC: ${CONTAINER_MAC} (expected xx:xx:xx:xx:xx:xx)"
        # The lowest bit of the first byte set means multicast, which cannot be an interface address
        (( (16#${CONTAINER_MAC:0:2} & 1) == 0 )) \
            || die "CONTAINER_MAC must not be a multicast address: ${CONTAINER_MAC}"
        [[ "${CONTAINER_MAC}" != "00:00:00:00:00:00" ]] || die "CONTAINER_MAC must not be all zeros"
    fi

    # ---- pasta network options ----
    # Name the container interface eth0 (pasta reuses the host interface name by default)
    # shellcheck disable=SC2034  # written to .env by compose_gen.sh
    PASTA_NETWORK="pasta:-I,eth0"
    if [[ -n "${CONTAINER_MAC}" ]]; then
        PASTA_NETWORK+=",--ns-mac-addr,${CONTAINER_MAC}"
    fi

    # ---- Misc ----
    CONTAINER_NAME="${CONTAINER_NAME:-casper-${TOOLFLOW_VERSION}}"
    [[ "${CONTAINER_NAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || die "Invalid CONTAINER_NAME: ${CONTAINER_NAME}"
    # shellcheck disable=SC2034  # written to .env by compose_gen.sh
    CONTAINER_HOSTNAME="casper-${TOOLFLOW_VERSION}"

    for k in VIVADO_DIR XIL_LICENSE_DIR WORK_DIR CONTAINER_PWD VNC_PASSWORD TZ; do
        _check_env_value "${k}" "${!k}"
    done
}
