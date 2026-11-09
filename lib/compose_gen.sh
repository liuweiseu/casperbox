# shellcheck shell=bash
# 生成 .generated/.env 和 .generated/compose.override.yml

# YAML 单引号字符串
_yaml_quote() {
    local s="${1//\'/\'\'}"
    printf "'%s'" "${s}"
}

generate_env_file() {
    local env_file="${GEN_DIR}/.env" k
    local -a keys=(
        "${TOOLFLOW_KEYS[@]}"
        HOST_UID HOST_GID CONTAINER_USER CONTAINER_NAME CONTAINER_HOSTNAME PASTA_NETWORK
        VIVADO_DIR VIVADO_MOUNT_OPTS XIL_LICENSE_DIR WORK_DIR
        BIND_ADDRESS SSH_PORT VNC_PORT
        CONTAINER_PWD VNC_PASSWORD VNC_GEOMETRY SHM_SIZE TZ
    )

    mkdir -p "${GEN_DIR}"
    # 包含密码，仅当前用户可读
    (
        umask 077
        {
            echo "# 由 casper.sh 自动生成，请勿手动修改"
            for k in "${keys[@]}"; do
                printf "%s='%s'\n" "${k}" "${!k}"
            done
        } > "${env_file}"
    )
    chmod 600 "${env_file}"
}

generate_override_file() {
    local out="${GEN_DIR}/compose.override.yml" item
    {
        echo "# 由 casper.sh 根据 user.conf 中的 EXTRA_MOUNTS / EXTRA_PORTS 自动生成，请勿手动修改"
        echo "services:"
        if (( ${#EXTRA_MOUNT_SPECS[@]} == 0 && ${#EXTRA_PORT_SPECS[@]} == 0 )); then
            echo "  casper: {}"
        else
            echo "  casper:"
            if (( ${#EXTRA_MOUNT_SPECS[@]} > 0 )); then
                echo "    volumes:"
                for item in "${EXTRA_MOUNT_SPECS[@]}"; do
                    echo "      - $(_yaml_quote "${item}")"
                done
            fi
            if (( ${#EXTRA_PORT_SPECS[@]} > 0 )); then
                echo "    ports:"
                for item in "${EXTRA_PORT_SPECS[@]}"; do
                    echo "      - $(_yaml_quote "${item}")"
                done
            fi
        fi
    } > "${out}"
}

save_state() {
    printf 'CURRENT_VERSION=%s\n' "${TOOLFLOW_VERSION}" > "${STATE_FILE}"
}
