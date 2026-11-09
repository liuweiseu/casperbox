#!/usr/bin/env bash
# 创建容器用户：UID/GID 与宿主机用户一致，加入 sudo 组。
# 密码不在构建时设置，由 entrypoint 在启动时根据 CONTAINER_PWD 设置。
set -euo pipefail

: "${CONTAINER_USER:?CONTAINER_USER 未设置}"
: "${USER_UID:?USER_UID 未设置}"
: "${USER_GID:?USER_GID 未设置}"

# 组：GID 已存在则复用，否则新建
if getent group "${USER_GID}" >/dev/null; then
    group_name="$(getent group "${USER_GID}" | cut -d: -f1)"
else
    groupadd -g "${USER_GID}" "${CONTAINER_USER}"
    group_name="${CONTAINER_USER}"
fi

# UID 被基础镜像中的其它用户占用时删除该用户（例如 ubuntu 镜像自带的 ubuntu 用户）
if existing="$(getent passwd "${USER_UID}" | cut -d: -f1)" && [[ -n "${existing}" ]] \
        && [[ "${existing}" != "${CONTAINER_USER}" ]]; then
    echo "UID ${USER_UID} 已被 ${existing} 占用，删除该用户"
    userdel -r "${existing}" 2>/dev/null || userdel "${existing}"
fi

useradd -m -u "${USER_UID}" -g "${group_name}" -G sudo -s /bin/bash "${CONTAINER_USER}"

home_dir="/home/${CONTAINER_USER}"

# 预先创建挂载点与工作目录，确保属主正确
mkdir -p "${home_dir}/work" "${home_dir}/.Xilinx" "${home_dir}/toolflow" "${home_dir}/.vnc"
chown -R "${USER_UID}:${USER_GID}" "${home_dir}"

# 非登录 shell（如 VNC 中打开的终端）也加载 toolflow 环境
cat >> "${home_dir}/.bashrc" <<'EOF'

# CASPER toolflow 环境（Vivado / conda / mlib_devel）
[ -r /etc/profile.d/casper.sh ] && . /etc/profile.d/casper.sh
EOF
chown "${USER_UID}:${USER_GID}" "${home_dir}/.bashrc"
