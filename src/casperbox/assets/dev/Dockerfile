# casperbox-dev image: a plain base container with SSH, VNC, an XFCE desktop and Firefox.
# Built by `casperbox-dev build`: BASE_IMAGE and PACKAGES come from the user's casper-dev.toml;
# the build context is the package's assets/ directory.

ARG BASE_IMAGE=docker.io/library/ubuntu:26.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# RUN instructions use /bin/sh: podman builds OCI images, which ignore the SHELL instruction.
# Keep RUN lines POSIX; anything that needs bash goes into a script run with `bash script.sh`.

# Build args are declared right before the step that uses them, so that changing
# one only invalidates the build cache from that step on.

# 1. Base packages: openssh-server / tigervnc / XFCE desktop / sudo
COPY dev/base.list /opt/casper/packages/base.list
COPY common/scripts/install_pkg_list.sh common/scripts/install_base.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_base.sh /opt/casper/packages/base.list

# 2. Firefox from Mozilla's APT repository (the distribution's package is a snap wrapper)
COPY dev/install_firefox.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_firefox.sh

# 3. Create the user (UID/GID match the host, for userns keep-id)
ARG CONTAINER_USER=casper
ARG USER_UID=1000
ARG USER_GID=1000
COPY common/scripts/create_user.sh /opt/casper/scripts/
RUN CREATE_TOOLFLOW_DIRS=0 bash /opt/casper/scripts/create_user.sh

# 4. Extra packages from casper-dev.toml (space-separated)
ARG PACKAGES=""
COPY dev/install_packages.sh /opt/casper/scripts/
RUN bash /opt/casper/scripts/install_packages.sh ${PACKAGES}

# 5. Runtime files: sshd config, profile, entrypoint, VNC xstartup
COPY common/rootfs/ /
COPY common/vnc/xstartup /opt/casper/vnc/xstartup
RUN chmod 755 /usr/local/bin/entrypoint.sh /opt/casper/vnc/xstartup \
 && chmod 644 /etc/profile.d/casper.sh /etc/ssh/sshd_config.d/casper.conf \
 && mkdir -p /etc/casper \
 && echo "CASPER_USER=${CONTAINER_USER}" > /etc/casper/container.env \
 && chmod 644 /etc/casper/container.env

LABEL casper.user="${CONTAINER_USER}" \
      casper.uid="${USER_UID}" \
      casper.gid="${USER_GID}"

EXPOSE 22 5901

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
