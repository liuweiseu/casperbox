#!/usr/bin/env bash
# 容器入口（以 root 运行）：
#   1. 根据 CONTAINER_PWD 设置用户密码，根据 VNC_PASSWORD 生成 VNC 密码
#   2. 启动 sshd（22）和 vncserver :1（5901）
#   3. 保持前台运行，收到停止信号时清理
set -euo pipefail

log() { echo "[entrypoint] $*"; }

# shellcheck disable=SC1091
. /etc/casper/toolflow.env
user="${CASPER_USER}"
group="$(id -gn "${user}")"
home_dir="/home/${user}"

: "${CONTAINER_PWD:?CONTAINER_PWD 未设置}"
VNC_PASSWORD="${VNC_PASSWORD:-${CONTAINER_PWD}}"
VNC_GEOMETRY="${VNC_GEOMETRY:-1920x1080}"
if [[ ! "${VNC_GEOMETRY}" =~ ^[0-9]+x[0-9]+$ ]]; then
    log "VNC_GEOMETRY 格式错误: ${VNC_GEOMETRY}，使用 1920x1080"
    VNC_GEOMETRY=1920x1080
fi

# ---- 密码 ----
log "设置用户 ${user} 的密码"
printf '%s:%s\n' "${user}" "${CONTAINER_PWD}" | chpasswd

log "设置 VNC 密码"
install -d -m 700 -o "${user}" -g "${group}" "${home_dir}/.vnc"
printf '%s\n' "${VNC_PASSWORD}" | vncpasswd -f > "${home_dir}/.vnc/passwd"
chown "${user}:${group}" "${home_dir}/.vnc/passwd"
chmod 600 "${home_dir}/.vnc/passwd"
if [[ ! -f "${home_dir}/.vnc/xstartup" ]]; then
    install -m 755 -o "${user}" -g "${group}" /opt/casper/vnc/xstartup "${home_dir}/.vnc/xstartup"
fi
unset CONTAINER_PWD VNC_PASSWORD

# ---- Vivado 路径（挂载路径与宿主机一致）----
# 支持两种布局：<VIVADO_DIR>/Vivado/<版本>/settings64.sh 或 <VIVADO_DIR>/settings64.sh
: "${VIVADO_DIR:?VIVADO_DIR 未设置}"
xilinx_path=""
composer_path=""
if [[ -r "${VIVADO_DIR}/Vivado/${VIVADO_VERSION}/settings64.sh" ]]; then
    xilinx_path="${VIVADO_DIR}/Vivado/${VIVADO_VERSION}"
    [[ -d "${VIVADO_DIR}/Model_Composer/${VIVADO_VERSION}" ]] \
        && composer_path="${VIVADO_DIR}/Model_Composer/${VIVADO_VERSION}"
elif [[ -r "${VIVADO_DIR}/settings64.sh" ]]; then
    xilinx_path="${VIVADO_DIR}"
else
    log "警告：在 ${VIVADO_DIR} 下未找到 Vivado ${VIVADO_VERSION} 的 settings64.sh，请检查 VIVADO_DIR"
fi
log "Vivado: ${xilinx_path:-<未找到>}"
{
    echo "# 由 entrypoint 在容器启动时生成"
    printf "VIVADO_DIR='%s'\n" "${VIVADO_DIR}"
    printf "XILINX_PATH='%s'\n" "${xilinx_path}"
    printf "COMPOSER_PATH='%s'\n" "${composer_path}"
} > /etc/casper/runtime.env
chmod 644 /etc/casper/runtime.env

# ---- sshd ----
ssh-keygen -A >/dev/null
mkdir -p /run/sshd
log "启动 sshd"
/usr/sbin/sshd

# ---- vncserver ----
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
log "启动 vncserver :1 (${VNC_GEOMETRY})"
su - "${user}" -c "vncserver :1 -geometry ${VNC_GEOMETRY} -depth 24 -localhost no -SecurityTypes VncAuth"

cleanup() {
    log "停止服务"
    su - "${user}" -c "vncserver -kill :1" >/dev/null 2>&1 || true
    pkill -TERM -x sshd 2>/dev/null || true
    exit 0
}
trap cleanup TERM INT

log "容器已就绪 (toolflow ${TOOLFLOW_VERSION})"
sleep infinity &
wait $!
