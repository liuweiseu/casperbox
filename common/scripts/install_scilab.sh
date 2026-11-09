#!/usr/bin/env bash
# 安装 Scilab：
#   1. 下载 SCILAB_URL 指向的 .tar.gz / .tar.xz 压缩包
#   2. 解压到 /opt/scilab（去掉压缩包中的顶层目录 scilab-<version>/）
#   3. 把可执行文件链接到容器用户的 ~/.local/bin/scilab
# 以 root 运行，需在 create_user.sh 之后执行。
set -euo pipefail

: "${SCILAB_VERSION:?SCILAB_VERSION 未设置}"
: "${SCILAB_URL:?SCILAB_URL 未设置}"
: "${CONTAINER_USER:?CONTAINER_USER 未设置}"

install_dir=/opt/scilab
user_group="$(id -gn "${CONTAINER_USER}")"
user_bin="/home/${CONTAINER_USER}/.local/bin"

case "${SCILAB_URL}" in
    *.tar.gz|*.tgz|*.tar.xz) ;;
    *) echo "错误：SCILAB_URL 应指向 .tar.gz 或 .tar.xz 压缩包: ${SCILAB_URL}" >&2; exit 1 ;;
esac

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
archive="${tmp_dir}/$(basename "${SCILAB_URL}")"

echo "下载 Scilab ${SCILAB_VERSION}: ${SCILAB_URL}"
curl -fL --retry 3 -o "${archive}" "${SCILAB_URL}"

# 解压到 /opt/scilab（tar 根据内容自动识别 gz / xz）
rm -rf "${install_dir}"
mkdir -p "${install_dir}"
tar -xf "${archive}" -C "${install_dir}" --strip-components=1

if [[ ! -x "${install_dir}/bin/scilab" ]]; then
    echo "错误：解压后未找到 ${install_dir}/bin/scilab，请检查压缩包内容" >&2
    exit 1
fi

# 链接到用户的 ~/.local/bin/scilab（目录属主为容器用户，便于 pip --user 等使用）
install -d -m 755 -o "${CONTAINER_USER}" -g "${user_group}" \
    "/home/${CONTAINER_USER}/.local" "${user_bin}"
ln -sfn "${install_dir}/bin/scilab" "${user_bin}/scilab"
chown -h "${CONTAINER_USER}:${user_group}" "${user_bin}/scilab"

echo "Scilab ${SCILAB_VERSION} 已安装到 ${install_dir}，链接: ${user_bin}/scilab"
