# CASPER toolflow m2021a 镜像
# 构建参数由 casper.sh 从 versions/m2021a/toolflow.conf 与 config/user.conf 读取，
# 经 compose.yml 的 build.args 传入，请勿直接修改默认值来改变版本。

ARG BASE_IMAGE=docker.io/library/ubuntu:20.04
FROM ${BASE_IMAGE}

# ---- toolflow 版本参数（来自 toolflow.conf）----
ARG TOOLFLOW_VERSION
ARG VIVADO_VERSION
ARG MINICONDA_INSTALLER
ARG PYTHON_VERSION
ARG CONDA_ENV_NAME
ARG MLIB_DEVEL_REPO
ARG MLIB_DEVEL_BRANCH
ARG DEVICE_TREE_XLNX_REPO
ARG DEVICE_TREE_XLNX_BRANCH
ARG GIT_CLONE_DEPTH=1
ARG SCILAB_VERSION
ARG SCILAB_URL

# ---- 用户参数（来自 user.conf / 宿主机）----
ARG CONTAINER_USER=casper
ARG USER_UID=1000
ARG USER_GID=1000

LABEL casper.toolflow.version="${TOOLFLOW_VERSION}" \
      casper.user="${CONTAINER_USER}" \
      casper.uid="${USER_UID}" \
      casper.gid="${USER_GID}"

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    CONDA_DIR=/opt/conda

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 1. 系统软件：openssh-server / tigervnc / firefox / 桌面 / Vivado 依赖
COPY common/scripts/install_base.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_base.sh

# 2. Scilab
COPY common/scripts/install_scilab.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_scilab.sh

# 3. 创建用户（UID/GID 与宿主机一致，配合 userns keep-id）
COPY common/scripts/create_user.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/create_user.sh

# 4. Miniconda
COPY common/scripts/install_miniconda.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_miniconda.sh

# 5. conda 环境 + mlib_devel（以普通用户身份执行，保证文件属主正确）
COPY common/scripts/setup_conda_env.sh common/scripts/config_mlib_devel.sh /opt/casper/scripts/
USER ${CONTAINER_USER}
RUN HOME="/home/${CONTAINER_USER}" bash /opt/casper/scripts/setup_conda_env.sh
RUN HOME="/home/${CONTAINER_USER}" bash /opt/casper/scripts/config_mlib_devel.sh
USER root

# 6. 运行时文件：sshd 配置、profile、entrypoint、VNC xstartup
COPY common/rootfs/ /
COPY common/vnc/xstartup /opt/casper/vnc/xstartup
RUN chmod 755 /usr/local/bin/entrypoint.sh /opt/casper/vnc/xstartup \
 && chmod 644 /etc/profile.d/casper.sh /etc/ssh/sshd_config.d/casper.conf \
 && mkdir -p /etc/casper \
 && { \
      echo "CASPER_USER=${CONTAINER_USER}"; \
      echo "CASPER_TOOLFLOW_DIR=/home/${CONTAINER_USER}/toolflow"; \
      echo "TOOLFLOW_VERSION=${TOOLFLOW_VERSION}"; \
      echo "VIVADO_VERSION=${VIVADO_VERSION}"; \
      echo "PYTHON_VERSION=${PYTHON_VERSION}"; \
      echo "CONDA_ENV_NAME=${CONDA_ENV_NAME}"; \
      echo "MLIB_DEVEL_BRANCH=${MLIB_DEVEL_BRANCH}"; \
      echo "DEVICE_TREE_XLNX_BRANCH=${DEVICE_TREE_XLNX_BRANCH}"; \
      echo "SCILAB_VERSION=${SCILAB_VERSION}"; \
    } > /etc/casper/toolflow.env \
 && chmod 644 /etc/casper/toolflow.env

EXPOSE 22 5901

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
