# casperbox

`casperbox` builds and runs CASPER toolflow development containers with rootless podman.

The image includes openssh-server, TigerVNC (XFCE desktop), Firefox, Miniconda (conda env),
mlib_devel, device-tree-xlnx and Scilab. Vivado and the license are mounted from the host.

The package also installs `casperbox-dev`, which builds plain base containers for testing
(see [casperbox-dev](#casperbox-dev)).

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

Unknown keys and wrong types (e.g. a port written as a string) are reported as errors, and so are
mounted directories that do not exist. Problems that do not stop the container from starting, such as a
mounted path you cannot read, are warnings: `up` and `restart` then ask before creating the container
(answer `y` to continue; `--yes` skips the question, e.g. in scripts).

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
| `casperbox up [--yes]` | Start (recreate) the container; builds the image if missing or outdated |
| `casperbox down` | Stop and remove the container |
| `casperbox restart [--yes]` | `down` + `up` |
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
| Anything | `casperbox up` |

`casperbox up` rebuilds the image automatically when it is out of date: when the build files
(Dockerfile, scripts, package lists), the toolflow version, `container.user` or the host UID/GID
changed since the last build. `casperbox status` shows whether the image is up to date.

## casperbox-dev

`casperbox-dev` gives developers a plain container to test software in: a base OS with SSH, a VNC
(XFCE) desktop, Firefox and the packages you list, but no toolflow (no Vivado, mlib_devel, conda or Scilab)
and no directories mounted by default.

```bash
mkdir ~/dev-test && cd ~/dev-test
casperbox-dev init                 # creates casper-dev.toml (default os: ubuntu:26.04)
vi casper-dev.toml                 # set os, packages, mounts
casperbox-dev up
casperbox-dev shell
```

`casper-dev.toml` has the same `[container]`, `[ports]`, `[vnc]`, `[[mounts]]` and `[[extra_ports]]`
sections as `casperbox.toml`, plus:

| Key | Default | Description |
|---|---|---|
| `os` | `ubuntu:26.04` | Base image, `name:tag` of any apt-based image (Ubuntu / Debian) |
| `packages` | `[]` | Extra apt packages installed into the image |

- Find image names and tags on [Docker Hub](https://hub.docker.com/_/ubuntu) or with
  `podman search --list-tags docker.io/library/ubuntu`. `casperbox-dev list` shows the tested images;
  others work with a warning.
- Before building, the base image is pulled and checked to be apt-based.
- Firefox comes from Mozilla's APT repository (packages.mozilla.org): on Ubuntu 22.04+ the
  distribution's `firefox` package is a snap wrapper, which does not work in containers.
- Default ports are 2322 (SSH) and 5902 (VNC), so it can run next to a `casperbox` container.
- Each config gets its own image (`casperbox-dev:<container.name>`); changing `os` or `packages`
  rebuilds it on the next `casperbox-dev up`.
- All other commands (`build`, `up`, `down`, `restart`, `shell`, `status`, `logs`, `config`, `ps`)
  work as in `casperbox`; `casperbox-dev init --os IMAGE` picks the base image.

## Development

```
src/casperbox/
├── cli.py             Typer commands
├── config.py          casperbox.toml parsing and validation
├── podman.py          podman build/run arguments and container queries
├── versions.py        toolflow versions in assets/versions
├── templates/         casperbox.toml and casper-dev.toml templates
└── assets/            build context (see below)
tests/                 pytest tests; smoke/ container smoke test
```

```bash
uv sync
uv run pytest
uv run casperbox --help
uv tool install -e .           # installed casperbox uses this source tree
```

### Tests

CI (`.github/workflows/ci.yml`) runs on every push and pull request to `main` and `dev`:

| Job | What it checks |
|---|---|
| Lint | ruff on the Python code, shellcheck on all shell scripts |
| Unit tests | pytest on Python 3.11 – 3.14; both CLIs start |
| casperbox-dev container | for each tested OS: build and start a container, then check SSH password login, VNC, an extra forwarded port, a read-only mount, the MAC address, the host UID/GID, sudo and Firefox |
| casperbox container | the same for m2021a (with a stub Vivado), plus the conda env, `XILINX_PATH`, mlib_devel, device-tree-xlnx, Scilab and a writable `~/work` |

The container jobs only check that the environment comes up; toolflow functionality is not tested.
They run `tests/smoke/smoke.sh`, which also works on a host (needs `sshpass` and `curl`; the test
container is called `smoke-<tool>`, uses ports 22022/25901/28888 and is removed afterwards):

```bash
tests/smoke/smoke.sh casperbox-dev --os ubuntu:24.04
tests/smoke/smoke.sh casperbox
```

### Build files (`src/casperbox/assets/`)

```
common/packages/software.list   base apt packages, installed for every toolflow version
common/scripts/                 build scripts (install_pkg_list.sh installs a package list)
common/rootfs/                  sshd config, profile, entrypoint
toolflow/Dockerfile             Dockerfile shared by all toolflow versions
versions/<version>/
    toolflow.toml               version definition (passed as build args)
    pkgs.list                   optional: extra apt packages of this version (e.g. Vivado runtime deps)
    Dockerfile                  optional: replaces toolflow/Dockerfile for this version
dev/                            casperbox-dev: Dockerfile, base.list, install scripts
```

Package lists have one apt package per line; `#` starts a comment and a trailing `?` marks an
optional package (installed only if the OS release has it). Packages are installed in this order:
`common/packages/software.list`, then `versions/<version>/pkgs.list`.

- Adding a package for every version: add it to `common/packages/software.list`.
- Adding a package for one version: add it to `versions/<version>/pkgs.list`.
- Adding a toolflow version: create `versions/<new>/toolflow.toml` (copy m2021a's; `TOOLFLOW_VERSION`
  must match the directory name) and, if needed, `pkgs.list`. A Dockerfile is only needed if the
  version's build steps differ.

After any change, `casperbox up` rebuilds the image automatically.

The shell-based tool (`casper.sh`) is available at tag `v0.1.0`.
