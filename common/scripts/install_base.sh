#!/usr/bin/env bash
# 安装系统软件：openssh-server、tigervnc、firefox、XFCE 桌面以及 Vivado/toolflow 依赖
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl wget git sudo locales tzdata \
    bzip2 xz-utils unzip file less vim nano procps psmisc \
    iproute2 iputils-ping net-tools lsb-release \
    build-essential make gcc g++ device-tree-compiler \
    openssh-server \
    tigervnc-standalone-server tigervnc-common \
    xfce4 xfce4-terminal dbus-x11 x11-xserver-utils xfonts-base fonts-dejavu \
    firefox \
    libtinfo5 libncurses5 libx11-6 libxext6 libxrender1 libxtst6 libxi6 libxft2 \
    libxrandr2 libxcursor1 libxinerama1 libsm6 libice6 \
    libfreetype6 libfontconfig1 libglib2.0-0 libgtk2.0-0 libgtk-3-0 \
    libcanberra-gtk-module libcanberra-gtk3-module \
    libasound2 libgl1 libglu1-mesa graphviz

# locale
sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen
locale-gen
update-locale LANG=en_US.UTF-8

# sshd：容器内没有 auditd，pam_loginuid 设为 optional 以避免登录失败
mkdir -p /run/sshd
sed -i 's/^\(session\s\+\)required\(\s\+pam_loginuid.so\)/\1optional\2/' /etc/pam.d/sshd

apt-get clean
rm -rf /var/lib/apt/lists/*
