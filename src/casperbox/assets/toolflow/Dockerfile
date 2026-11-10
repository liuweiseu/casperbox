# CASPER toolflow image, shared by all toolflow versions.
# Built by `casperbox build`: build args come from versions/<version>/toolflow.toml and the
# user's casperbox.toml; the build context is the package's assets/ directory.
# A version that needs different build steps can provide its own versions/<version>/Dockerfile.

ARG BASE_IMAGE=docker.io/library/ubuntu:20.04
FROM ${BASE_IMAGE}

# ---- toolflow version args (from toolflow.toml) ----
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

# ---- user args (from casper.toml / host) ----
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

# RUN instructions use /bin/sh: podman builds OCI images, which ignore the SHELL instruction.
# Keep RUN lines POSIX; anything that needs bash goes into a script run with `bash script.sh`.

# 0. apt mirror ([build] apt_mirror in the config; empty = the base image's default sources)
ARG APT_MIRROR=""
COPY common/scripts/set_apt_mirror.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/set_apt_mirror.sh

# 1. Base packages for every version: openssh-server / tigervnc / firefox / desktop / tools
COPY common/packages/software.list /opt/casper/packages/
COPY common/scripts/apt_update.sh common/scripts/install_pkg_list.sh common/scripts/install_base.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_base.sh /opt/casper/packages/software.list

# 1b. Packages this version needs in addition (versions/<version>/pkgs.list, optional),
#     e.g. the Vivado runtime dependencies
COPY versions/${TOOLFLOW_VERSION}/ /opt/casper/version/
RUN if [ -f /opt/casper/version/pkgs.list ]; then \
        bash /opt/casper/scripts/install_pkg_list.sh /opt/casper/version/pkgs.list; \
    else \
        echo "No versions/${TOOLFLOW_VERSION}/pkgs.list"; \
    fi

# 2. Create the user (UID/GID match the host, for userns keep-id)
COPY common/scripts/create_user.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/create_user.sh

# 3. Scilab: extract to /opt/scilab, link to ~/.local/bin/scilab (requires the user)
COPY common/scripts/install_scilab.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_scilab.sh

# 4. Miniconda
COPY common/scripts/install_miniconda.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_miniconda.sh

# 5. conda env + mlib_devel (run as the regular user so file ownership is correct)
COPY common/scripts/setup_conda_env.sh common/scripts/config_mlib_devel.sh /opt/casper/scripts/
USER ${CONTAINER_USER}
RUN HOME="/home/${CONTAINER_USER}" bash /opt/casper/scripts/setup_conda_env.sh
RUN HOME="/home/${CONTAINER_USER}" bash /opt/casper/scripts/config_mlib_devel.sh
USER root

# 6. Runtime files: sshd config, profile, entrypoint, VNC xstartup
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

# 7. Version-specific fixes (versions/<version>/patch.sh, optional), run as root last so that
#    they can change anything set up above; the build args are available as environment variables
RUN if [ -f /opt/casper/version/patch.sh ]; then \
        bash /opt/casper/version/patch.sh; \
    else \
        echo "No versions/${TOOLFLOW_VERSION}/patch.sh"; \
    fi

EXPOSE 22 5901

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
