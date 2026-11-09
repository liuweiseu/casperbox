# casperbox

`casperbox` builds and runs CASPER toolflow development containers with rootless podman.

The image includes openssh-server, TigerVNC (XFCE desktop), Firefox, Miniconda (conda env),
mlib_devel, device-tree-xlnx and Scilab. Vivado and the license are mounted from the host.

## Supported versions

| Version | Base image | Vivado | Python | mlib_devel | device-tree-xlnx | Scilab |
|---|---|---|---|---|---|---|
| m2021a | Ubuntu 20.04 | 2021.1 | 3.14 | scilab-dev-ai | xlnx_rel_v2021.1 | 2024.1.0 |

## Host requirements

- [uv](https://docs.astral.sh/uv/) (installs without sudo)
- rootless podman >= 4.4 (run as a regular user) and the passt package (provides pasta)
- An installed Vivado (version in the table above) and a Xilinx license

## Host setup

Log in directly (SSH or desktop) as the user that runs podman, then run:

```bash
loginctl enable-linger "$(id -un)"
podman system migrate
podman run --rm docker.io/library/ubuntu:20.04 true   # check: must finish without errors
```

## Install

```bash
uv tool install git+https://github.com/<owner>/casperbox
# or from a local clone
uv tool install ./casperbox
```

Upgrade with `uv tool upgrade casperbox`.

## Quick start

```bash
mkdir ~/casper-a && cd ~/casper-a
casperbox init                    # creates casperbox.toml
vi casperbox.toml                 # set vivado.dir / paths.license_dir / ports
casperbox up                      # builds the image on first use, then starts the container
```

Connect:

- SSH: `ssh -p <ports.ssh> casper@<host>`, password `container.password` (default `casper`)
- VNC: `<host>:<ports.vnc>`, password `container.vnc_password` (default `casper`)
- Local: `casperbox shell`

## Configuration

`casperbox init` writes a commented `casperbox.toml` (permissions 600, it holds passwords).
Relative paths in it are relative to the file's directory.

| Key | Default | Description |
|---|---|---|
| `version` | | Toolflow version (`casperbox list`) |
| `container.name` | `casper-<version>` | Container name and hostname |
| `container.user` | `casper` | Container user |
| `container.password` / `vnc_password` | `casper` | SSH/sudo and VNC (6-8 chars) passwords |
| `container.mac` | random | MAC of `eth0` |
| `container.shm_size` / `tz` | `2g` / `UTC` | |
| `vivado.dir` / `vivado.mount` | / `ro` | Vivado install, mounted at the same path |
| `paths.license_dir` | `~/.Xilinx` | Mounted at `~/.Xilinx` |
| `paths.work_dir` | `./work` | Mounted at `~/work` (created if missing) |
| `ports.bind_address` | `0.0.0.0` | |
| `ports.ssh` / `ports.vnc` | `2222` / `5901` | Host ports for container 22 / 5901 |
| `vnc.geometry` | `1920x1080` | |
| `[[mounts]]` | | Extra mounts: `host`, `container` (`~/` = home), `mode` |
| `[[extra_ports]]` | | Extra ports: `host`, `container`, `protocol` |

Unknown keys and wrong types (e.g. a port written as a string) are reported as errors.

### Several containers

Each config file is one container. Use another directory or file, and give it its own
`container.name` and ports:

```bash
casperbox init lab.toml           # edit container.name, ports.ssh, ports.vnc
casperbox -c lab.toml up
casperbox ps                      # all casperbox containers, from any directory
```

`casperbox` refuses to recreate or remove a container that was created from a different config file.

## Commands

| Command | Description |
|---|---|
| `casperbox list` | List supported toolflow versions |
| `casperbox init [FILE] [--toolflow VER] [--force]` | Create a config file (default `casperbox.toml`) |
| `casperbox build [--no-cache]` | Build the image |
| `casperbox up` | Start (recreate) the container; builds the image if missing |
| `casperbox down` | Stop and remove the container |
| `casperbox restart` | `down` + `up` |
| `casperbox shell` | Open a login shell as the container user |
| `casperbox status` | Container status and ports |
| `casperbox logs [-f]` | Container logs |
| `casperbox config` | Resolved config and podman commands (passwords masked) |
| `casperbox ps` | List all casperbox containers |

`-c FILE` selects the config file (before or after the command); the default is `./casperbox.toml`.

## Inside the container

```
~/toolflow/
├── mlib_devel/            # includes the generated startsg.local
├── device-tree-xlnx/
└── src/                   # -e dependencies from requirements.txt
```

Shells automatically load Vivado `settings64.sh` and the variables in `startsg.local`, and activate the conda env `casper`.
Scilab is in `/opt/scilab`; run `scilab` (linked in `~/.local/bin`).

> `~/toolflow` is part of the image, not a mount. `down`/`up` recreate the container and
> discard changes made there; keep your projects under `~/work`.

## Networking (pasta)

The container uses pasta networking (no bridge network). Ports are forwarded with `-p`,
the interface is named `eth0`, and the container sees the same IP as the host.
`container.mac` is applied with pasta's `--ns-mac-addr` (e.g. for a MAC-locked node-locked license);
the interface exists only in the container's network namespace, so reusing the host NIC's MAC is safe.

## After changing the config

| Change | Action |
|---|---|
| Directories, ports, passwords, MAC, mounts | `casperbox up` |
| `container.user`, or a different host user (UID/GID changed) | `casperbox build`, then `casperbox up` |

## Development

```
src/casperbox/
├── cli.py             Typer commands
├── config.py          casperbox.toml parsing and validation
├── podman.py          podman build/run arguments and container queries
├── versions.py        toolflow versions in assets/versions
├── templates/         casperbox.toml template
└── assets/            build context: Dockerfiles, build scripts, rootfs, software.list
tests/                 pytest tests
```

```bash
uv sync
uv run pytest
uv run casperbox --help
uv tool install -e .           # installed casperbox uses this source tree
```

Adding apt packages: add one per line to `assets/common/packages/software.list`, then `casperbox build`.

Adding a toolflow version: copy `assets/versions/m2021a` to `assets/versions/<new>`, edit
`toolflow.toml` (`TOOLFLOW_VERSION` must match the directory name) and the Dockerfile as needed.

The shell-based tool (`casper.sh`) is available at tag `v0.1.0`.
