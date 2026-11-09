# casper-container

Build and run a CASPER toolflow development container with rootless podman + podman compose.

The image includes openssh-server, TigerVNC (XFCE desktop), Firefox, Miniconda (conda env),
mlib_devel, device-tree-xlnx and Scilab. Vivado and the license are mounted from the host.

## Supported versions

| Version | Base image | Vivado | Python | mlib_devel | device-tree-xlnx | Scilab |
|---|---|---|---|---|---|---|
| m2021a | Ubuntu 20.04 | 2021.1 | 3.14 | [liuweiseu/mlib_devel](https://github.com/liuweiseu/mlib_devel) `scilab-dev-ai` | xlnx_rel_v2021.1 | 2024.1.0 |

## Host requirements

- rootless podman >= 4.4 (run as a regular user) and the passt package (provides pasta)
- podman-compose >= 1.1.0 (`podman compose` is forced to use podman-compose; docker-compose does not support `userns keep-id`)
- An installed Vivado (version in the table above) and a Xilinx license

## Host setup

Log in directly (SSH or desktop) as the user that runs podman (do not switch with `su`), then run:

```bash
loginctl enable-linger                      # keep the user session so containers survive logout
grep "^$(id -un):" /etc/subuid /etc/subgid  # should print one line each; otherwise ask an admin
podman system migrate
podman info --format '{{.Host.CgroupManager}}'   # OK if it prints systemd with no WARN
```

## Quick start

```bash
cp config/user.conf.example config/user.conf
vi config/user.conf            # set VIVADO_DIR / XIL_LICENSE_DIR / WORK_DIR / ports

./casper.sh build              # build the image (auto-selected if there is only one version)
./casper.sh up                 # start the container
```

Connect:

- SSH: `ssh -p <SSH_PORT> casper@<host>`, password `CONTAINER_PWD` (default `casper`)
- VNC: `<host>:<VNC_PORT>`, password `VNC_PASSWORD` (default `casper`)
- Local: `./casper.sh shell`

## Commands

| Command | Description |
|---|---|
| `./casper.sh list` | List supported versions |
| `./casper.sh build [version] [--no-cache]` | Build the image |
| `./casper.sh up [version]` | Start (recreate) the container; builds the image if missing |
| `./casper.sh down` | Stop and remove the container |
| `./casper.sh restart` | Restart the container |
| `./casper.sh shell` | Open a shell in the container as the container user |
| `./casper.sh status` / `logs` | Status / logs |
| `./casper.sh config` | Show the merged compose config (passwords masked) |
| `./casper.sh -c other.conf up` | Use another config file |

## Directories and mounts

| Host (user.conf) | Container |
|---|---|
| `VIVADO_DIR` | Same path as on the host (read-only by default) |
| `XIL_LICENSE_DIR` | `~/.Xilinx` |
| `WORK_DIR` | `~/work` |
| `EXTRA_MOUNTS` (optional) | User-defined |

The toolflow lives in `~/toolflow` inside the container:

```
~/toolflow/
├── mlib_devel/            # includes the generated startsg.local
├── device-tree-xlnx/
└── src/                   # -e dependencies from requirements.txt
```

Shells automatically load Vivado `settings64.sh` and the variables in `startsg.local`, and activate the conda env `casper`.

Scilab is extracted to `/opt/scilab` and linked as `~/.local/bin/scilab` (`~/.local/bin` is on PATH),
so just run `scilab`.

> Note: `~/toolflow` is part of the image, not a mount. `down`/`up` recreate the container and
> discard changes made there; keep your projects under `~/work`.

## Networking (pasta)

The container uses rootless podman's pasta network mode (`network_mode: pasta:...`); no bridge network is created:

- SSH / VNC / `EXTRA_PORTS` are still forwarded with `-p`; podman hands them to pasta;
- the container interface is named `eth0` (pasta would otherwise reuse the host interface name);
- the container sees the same IP as the host.

### Fixed MAC address

Set `CONTAINER_MAC=xx:xx:xx:xx:xx:xx` in `user.conf` and the script applies it to the container's
`eth0` via pasta's `--ns-mac-addr`, e.g. for a MAC-locked Xilinx node-locked license. Leave empty for a random one.

> The pasta interface exists only in the container's own network namespace, so reusing the MAC of
> the host's physical NIC does not cause conflicts on the LAN.

## After changing the config

| Change | Action |
|---|---|
| Directories, ports, passwords, `CONTAINER_MAC`, `EXTRA_*` | `./casper.sh up` |
| `CONTAINER_USER`, or a different host user (UID/GID changed) | `./casper.sh build`, then `./casper.sh up` |

## Repository layout

```
casper.sh              entry script
compose.yml            base compose definition (variables from .generated/.env)
config/                user config
versions/<ver>/        per-version Dockerfile and toolflow.conf (protected by SHA256SUMS, do not modify)
common/scripts/        image build scripts
common/rootfs/         files copied into the image (sshd config, profile, entrypoint)
common/vnc/xstartup    VNC session script
lib/                   function library for casper.sh
tools/                 maintenance tools
.generated/            generated .env and compose.override.yml
```

## Maintainers: adding a version

1. Copy `versions/m2021a` to `versions/<new version>`, edit `toolflow.conf` (`TOOLFLOW_VERSION` must match the directory name) and adjust the Dockerfile as needed.
2. Run `tools/update-checksums.sh` to update `versions/SHA256SUMS`.
