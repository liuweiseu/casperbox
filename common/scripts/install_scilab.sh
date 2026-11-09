#!/usr/bin/env bash
# 下载并解压 Scilab 到 /opt/scilab-<version>，建立 /opt/scilab 链接并把命令加入 PATH
set -euo pipefail

: "${SCILAB_VERSION:?SCILAB_VERSION 未设置}"
: "${SCILAB_URL:?SCILAB_URL 未设置}"

install_dir="/opt/scilab-${SCILAB_VERSION}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

echo "下载 Scilab ${SCILAB_VERSION}: ${SCILAB_URL}"
curl -fL --retry 3 -o "${tmp_dir}/scilab.tar" "${SCILAB_URL}"

# tar 自动识别 gz / xz 压缩
tar -xf "${tmp_dir}/scilab.tar" -C /opt

if [[ ! -x "${install_dir}/bin/scilab" ]]; then
    echo "错误：解压后未找到 ${install_dir}/bin/scilab，请检查 SCILAB_VERSION 与压缩包目录名是否一致" >&2
    exit 1
fi

ln -sfn "${install_dir}" /opt/scilab
for exe in scilab scilab-cli scilab-adv-cli xcos; do
    if [[ -x "/opt/scilab/bin/${exe}" ]]; then
        ln -sf "/opt/scilab/bin/${exe}" "/usr/local/bin/${exe}"
    fi
done

echo "Scilab 已安装到 ${install_dir}"
