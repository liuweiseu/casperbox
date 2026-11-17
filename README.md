# casperbox

`casperbox` builds and runs CASPER toolflow development containers with rootless podman.

The image includes openssh-server, TigerVNC (XFCE desktop), Firefox, Miniforge (conda env),
mlib_devel, device-tree-xlnx and Scilab. Vivado and the license are mounted from the host.

The package also installs `basebox`, a general-purpose container builder: any apt-based OS with SSH
and a VNC desktop, your packages and tools, optionally GPUs and fast NICs; also useful for early tests
of CASPER toolflow components (see [basebox](#basebox)).

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
| `container.memory` / `cpus` / `cpuset` | no limit | Resource limits, e.g. `"64g"` / `16` / `"0-15"` (see [Shared servers](#shared-servers)) |
| `vivado.dir` / `vivado.mount` | / `ro` | Vivado install, mounted at the same path: the Xilinx root (`Vivado/<version>/settings64.sh` up to 2024.x, `<version>/Vivado/settings64.sh` from 2025.1) or the Vivado directory itself |
| `paths.license_dir` | `~/.Xilinx` | Mounted at `~/.Xilinx` |
| `paths.work_dir` | `./work` | Mounted at `~/work` (created if missing) |
| `ports.bind_address` | `0.0.0.0` | |
| `ports.ssh` / `ports.vnc` | `0` / `0` in the generated file (`2222` / `5901` if missing) | Host ports for container 22 / 5901. `0`: `up` picks the first free port from 2222 / 5901 (skipping ports of other casperbox containers, even stopped ones) and writes it back into the config file |
| `vnc.geometry` | `1920x1080` | |
| `build.apt_mirror` | default servers | Ubuntu apt mirror for the image build, e.g. `http://us.archive.ubuntu.com/ubuntu` |
| `[[mounts]]` | | Extra mounts: `host`, `container` (`~/` = home), `mode` |
| `[[extra_ports]]` | | Extra ports: `host`, `container`, `protocol` |

Unknown keys and wrong types (e.g. a port written as a string) are reported as errors, and so are
mounted directories that do not exist. Problems that do not stop the container from starting, such as a
mounted path you cannot read, are warnings: `up` then asks before creating the container
(answer `y` to continue; `--yes` skips the question, e.g. in scripts).

If the container already exists, `up` asks before removing and recreating it: changes made in the
container outside the mounted directories (packages installed with apt, edits in `~/toolflow`, ...) are
lost. `--yes` skips this question too. `restart` only stops and starts the container: nothing is lost,
but config changes are only applied by `up`.

### Several containers

Each config file is one container. Use another directory or file, and give it its own
`container.name` and ports:

```bash
casperbox init lab.toml           # edit container.name; ports 0 = up picks free ones
casperbox -c lab.toml up
casperbox ps                      # all casperbox containers, from any directory
```

`casperbox` refuses to recreate or remove a container that was created from a different config file.

### Shared servers

By default a container can use all CPUs and all memory of the host. When several users run Vivado on
one server, give each container its own limits, so that one large run cannot take the memory of the
others (the kernel would then kill processes of any user):

```toml
[container]
memory = "64g"      # RAM limit (no swap on top of it); Vivado is killed in this container only
cpuset = "0-15"     # CPU cores; Vivado sees (nproc) and uses only these
cpus = 0            # alternatively a CPU time quota, e.g. 16 (nproc still shows all cores)
```

The limits need cgroup v2 with the controllers delegated to regular users; `up` checks this and
stops with an error otherwise. Many distributions delegate only `memory` and `pids`. The administrator
enables the others once per host:

```bash
sudo mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nDelegate=cpu cpuset io memory pids\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf
sudo systemctl daemon-reload      # then users log out and in again
```

Without limits (the default) none of this is needed. `status` shows the limits of the running container.

## Commands

| Command | Description |
|---|---|
| `casperbox list` | List toolflow versions (built-in and plugins) with their base OS, Vivado, Scilab and Python versions |
| `casperbox init [FILE] [--toolflow VER] [--force]` | Create a config file (default `casperbox.toml`) |
| `casperbox build [--no-cache]` | Build the image |
| `casperbox up [--yes]` | Start (recreate) the container; builds the image if missing or outdated |
| `casperbox down` | Stop and remove the container |
| `casperbox restart` | Stop and start the container; its data is kept, config changes are not applied |
| `casperbox commit [IMAGE] [-o FILE]` | Save the container (with the changes made in it) as an image, optionally also to a tar file |
| `casperbox shell` | Open a login shell as the container user |
| `casperbox status` | Container status and ports |
| `casperbox logs [-f]` | Container logs |
| `casperbox config` | Resolved config and podman commands (passwords masked) |
| `casperbox ps` | List all casperbox containers |
| `casperbox plugin new NAME [--from VER] [--dir DIR]` | Create a toolflow version plugin from a template |
| `casperbox plugin check NAME` | Check a plugin's files without building |
| `casperbox plugin path` | Show the directories searched for plugins |

`-c FILE` selects the config file (before or after the command); the default is `./casperbox.toml`.

### Saving a container as an image

```bash
casperbox commit                       # -> localhost/<container>:snapshot-<date>-<time>
casperbox commit mytoolflow:v1 -o mytoolflow.tar
podman load -i mytoolflow.tar          # on another machine
```

The image contains everything changed in the container (packages installed with apt, edits in
`~/toolflow`, ...), but not the mounted directories (`~/work`, `~/.Xilinx`, `[[mounts]]`).
It also contains the container's passwords and SSH host keys, so only share it with people who may
know them. `basebox commit` works the same way.

## Plugins: your own toolflow versions

A plugin is a toolflow version you define yourself, in the same layout as a built-in one
(see [Build files](#build-files-srccasperboxassets)). It shows up in `casperbox list` and is used like a
built-in version:

```bash
casperbox plugin new m2025a            # copy of the newest built-in version, in ~/.config/casperbox/versions/m2025a
vi ~/.config/casperbox/versions/m2025a/toolflow.toml   # Vivado version, base image, branches, ...
casperbox plugin check m2025a          # validate without building
casperbox init --toolflow m2025a       # then casperbox up as usual
```

- `--from VER` copies another version (built-in or plugin); the generated `README.md` explains the files.
- Plugins are searched in the directories of `CASPERBOX_PLUGIN_PATH` (separated by `:`, e.g. a
  directory shared by all users of a server) and in `~/.config/casperbox/versions`.
  `casperbox plugin new --dir DIR` creates one elsewhere.
- A plugin may not have the name of a built-in version or of a plugin in another directory.
- Changing a plugin's files makes `up` rebuild the image. Its patch scripts end up in the image, so keep
  shared plugin directories writable only by their maintainers.

## Inside the container

```
~/.startsg.local           # generated mlib_devel config, sourced by ~/.bashrc
~/.local/bin/startsg       # -> ~/toolflow/mlib_devel/startsg
~/toolflow/
├── mlib_devel/
├── device-tree-xlnx/
└── src/                   # -e dependencies from requirements.txt
```

Shells automatically load Vivado `settings64.sh` and the variables in `~/.startsg.local`, and activate the conda env `casper`
(with `pyqt6`, `pyqtgraph` and `rich` installed).
Scilab is in `/opt/scilab`; run `scilab` (linked in `~/.local/bin`).

> `~/toolflow` is part of the image, not a mount. `down`/`up` recreate the container and
> discard changes made there; keep your projects under `~/work`.

## Networking (pasta)

The container uses pasta networking (no bridge network). Ports are forwarded with `-p`,
the interface is named `eth0`, and the container sees the same IP as the host.
`container.mac` is applied with pasta's `--ns-mac-addr` (e.g. for a MAC-locked node-locked license);
the interface exists only in the container's network namespace, so reusing the host NIC's MAC is safe.
`basebox` can also share the host's network instead (`network.mode = "host"`, see
[Fast NICs](#fast-nics-host-network-and-rdma)).

## After changing the config

| Change | Action |
|---|---|
| Anything | `casperbox up` |

`casperbox up` rebuilds the image automatically when it is out of date: when the build files
(Dockerfile, scripts, package lists), the toolflow version, `container.user` or the host UID/GID
changed since the last build. `casperbox status` shows whether the image is up to date.

## basebox

`basebox` builds general-purpose containers, e.g. to test software in: a base OS with SSH, a VNC
(XFCE) desktop, Firefox and the packages you list, but no toolflow (no Vivado, mlib_devel, conda or Scilab)
and no directories mounted by default.

```bash
mkdir ~/basebox-test && cd ~/basebox-test
basebox init                       # creates basebox.toml (default os: ubuntu:26.04)
vi basebox.toml                    # set os, packages, mounts
basebox up
basebox shell
```

`basebox.toml` has the same `[container]`, `[ports]`, `[vnc]`, `[build]`, `[[mounts]]` and `[[extra_ports]]`
sections as `casperbox.toml`, plus:

| Key | Default | Description |
|---|---|---|
| `os` | `ubuntu:26.04` | Base image, `name:tag` of any apt-based image (Ubuntu / Debian) |
| `packages` | `[]` | Extra apt packages installed into the image, also from the repositories below. In `[build]` (as in the generated config) or at the top of the file |
| `[[apt_repos]]` | none | Third-party apt repositories, added before `packages` are installed |
| `[[tools]]` | none | Tools installed by a script while the image is built (see below) |
| `container.gpus` | `""` | NVIDIA GPUs: `"0"`, `"0,1"` or `"all"` (see [GPUs](#gpus)) |
| `container.rdma` | `""` | RDMA devices: `"all"` or `"mlx5_0,mlx5_1"` |
| `network.mode` | `"pasta"` | `"host"`: share the host's network, every NIC at full speed (see [Fast NICs](#fast-nics-host-network-and-rdma)) |
| `ports.bind_address` | `0.0.0.0` | Also accepts a network interface name, e.g. `"eno1"` |

- Find image names and tags on [Docker Hub](https://hub.docker.com/_/ubuntu) or with
  `podman search --list-tags docker.io/library/ubuntu`. `basebox list` shows the tested images;
  others work with a warning.
- Before building, the base image is pulled and checked to be apt-based.
- Firefox comes from Mozilla's APT repository (packages.mozilla.org): on Ubuntu 22.04+ the
  distribution's `firefox` package is a snap wrapper, which does not work in containers.
- The generated config sets `ports.ssh` and `ports.vnc` to 0, so `up` picks free host ports (and writes
  them back), and it can run next to a `casperbox` container. Without a `[ports]` section the defaults
  are 2322 (SSH) and 5902 (VNC).
- basebox was called `casperbox-dev` before 0.5.0, and its config `casperbox-dev.toml` (or
  `casper-dev.toml`). An old config in the current directory is still found (with a warning); after
  `mv casperbox-dev.toml basebox.toml` its existing container still belongs to it. Configs written by
  the old `init` keep their container name (`casper-dev-<os>`); the image is now called
  `basebox:<container.name>`, so the next `up` builds it once. Remove the old image with
  `podman rmi casperbox-dev:<container.name>`.
- Each config gets its own image (`basebox:<container.name>`); changing `os`, `packages`,
  `apt_repos` or `tools` rebuilds it on the next `basebox up`.
- All other commands (`build`, `up`, `down`, `restart`, `shell`, `status`, `logs`, `config`, `ps`)
  work as in `casperbox`; `basebox init --os IMAGE` picks the base image.

### Third-party apt repositories

For software that is not in the Ubuntu archive but has its own apt repository. Take the values
from the vendor's install instructions: `key` is the URL their `curl` command downloads, and their
`deb [signed-by=...] <url> <suite> <components...>` line gives the rest. The packages go into
`packages`:

```toml
packages = ["yazi", "code"]

[[apt_repos]]
name = "yazi"
key = "https://yazi-rs.github.io/builds/yazi-keyring.gpg"
url = "https://yazi-rs.github.io/builds/"
suite = "stable"

[[apt_repos]]
name = "vscode"
key = "https://packages.microsoft.com/keys/microsoft.asc"
url = "https://packages.microsoft.com/repos/code"
suite = "stable"
```

| Key | Default | Description |
|---|---|---|
| `name` | | Lowercase letters, digits and `-`; names `/etc/apt/sources.list.d/<name>.sources` and `/etc/apt/keyrings/<name>.asc` / `.gpg` |
| `key` | | `https://` URL of the signing key, binary or ASCII-armored |
| `url` | | Repository URL |
| `suite` | OS codename | e.g. `stable`; empty = the base image's codename (`noble`, ...), for repositories per Ubuntu release; `./` for a flat repository |
| `components` | `["main"]` | none for a flat repository |
| `key_fingerprint` | | Optional. If set, the build fails unless the downloaded key has this fingerprint (40 hex digits, spaces allowed). Use the fingerprint the vendor publishes; `curl -fsSL <key> \| gpg --show-keys` shows the downloaded key's |

The repositories stay in the image, so `sudo apt upgrade` in the container updates their packages too.
`[build] apt_mirror` only replaces the Ubuntu servers.

### Tools that are not apt packages: `[[tools]]`

Each `[[tools]]` block installs one tool with a bash script while the image is built, for what apt
does not have (release tarballs, vendor install scripts, per-user setup). The generated config has
two commented-out examples (lazygit as root, shell aliases as the user); remove the leading `# ` to
use them.

```toml
[[tools]]
name = "lazygit"
version = "0.65.1"
script = '''
curl -fsSLO "https://github.com/jesseduffield/lazygit/releases/download/v${VERSION}/lazygit_${VERSION}_linux_x86_64.tar.gz"
tar -xzf "lazygit_${VERSION}_linux_x86_64.tar.gz" -C /usr/local/bin lazygit
'''
```

| Key | Default | Description |
|---|---|---|
| `name` | | Lowercase letters, digits, `.`, `_`, `-`; unique |
| `version` | `""` | Passed to the script as `$VERSION` |
| `run_as` | `"root"` | `"root"`, or `"user"`: the container user, with `HOME` set and `~/.local/bin` on `PATH` |
| `script` | | The commands, run with bash and `set -euo pipefail`. Write it in `'''...'''` so backslashes and `$` stay as written |

- All `root` tools run first, then the `user` tools, each group in the order written, so a tool can
  rely on the ones above it. They run after `[[apt_repos]]` and `packages`. A failing script stops the build.
- A script sees `$VERSION`, `$DEB_ARCH` (`amd64` / `arm64`), `$CONTAINER_USER` and `$HOME`.
- Changing a script, a version or the order rebuilds the image on the next `up`.

### GPUs

`container.gpus = "0"`, `"0,1"` or `"all"` passes NVIDIA GPUs into the container, with
`--ulimit nofile=65535`. Set `shm_size` (e.g. `"32g"`) for data loaders. `up` checks that `nvidia-ctk`
exists and that every device is in the CDI list, and prints the commands below if not. The GPUs are
fixed when the container is created: edit `gpus` and run `basebox up` to recreate it (no rebuild).
`basebox status` shows the GPUs of the running container.

basebox gives the GPUs to podman as **CDI** (Container Device Interface) devices,
`nvidia.com/gpu=N`. A CDI spec file, normally `/etc/cdi/nvidia.yaml`, describes the devices; podman
reads it and puts the `/dev/nvidia*` device nodes and the driver's libraries (`libcuda`, `nvidia-smi`,
...) into the container. The host needs, set up once by an administrator:

| What | Why | Check |
| --- | --- | --- |
| The **NVIDIA driver** | The container reuses the host's driver | `nvidia-smi` lists the GPUs |
| **`nvidia-container-toolkit`** | Provides `nvidia-ctk`, which writes the CDI spec | `nvidia-ctk --version` |
| A **CDI spec** | Names the devices (`nvidia.com/gpu=0`, ..., `=all`) | `nvidia-ctk cdi list` |

Not needed: the CUDA toolkit on the host (CUDA libraries come from the container, e.g. pip or conda
packages), Docker, or root to run the container.

1. **The toolkit**, on Debian / Ubuntu:

   ```bash
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
     | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
     | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
     | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   ```

2. **The CDI spec**; generate it again after every driver upgrade and whenever GPUs are added or removed:

   ```bash
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   nvidia-ctk cdi list            # nvidia.com/gpu=0 ... nvidia.com/gpu=all
   ```

3. **Try it with podman alone**: `podman run --rm --device nvidia.com/gpu=all docker.io/library/ubuntu:24.04 nvidia-smi -L`

| Symptom | Likely cause and fix |
| --- | --- |
| `nvidia-ctk is not installed` | Install `nvidia-container-toolkit` (step 1). |
| `CDI device(s) not found: nvidia.com/gpu=N` | No spec yet, or an index that does not exist: run step 2 and compare with `nvidia-ctk cdi list`. |
| `unresolvable CDI devices` from podman | The spec is not in `/etc/cdi` or `/var/run/cdi`, or it is unreadable for your user. |
| `nvidia-smi` fails in the container after a driver upgrade | The spec is from the old driver: regenerate it (step 2), then `basebox up`. |

### Fast NICs: host network and RDMA

By default the container has its own network (`network.mode = "pasta"`, a user-space stack of a few
Gbit/s) and only the SSH, VNC and extra ports are published. To use a 100G NIC at full speed (iperf3,
UDP data streams, RDMA) while reaching SSH and VNC over the management NIC:

```toml
[network]
mode = "host"              # the container shares the host's network: every NIC, at full speed

[ports]
bind_address = "eno1"      # the management NIC: an IP, or an interface name
ssh = 0                    # SSH and VNC listen on these host ports (0 = up picks free ones)
vnc = 0

[container]
rdma = "all"               # or "mlx5_0,mlx5_1"; see `ibv_devices` on the host
```

- **The host's addresses.** In host mode the container uses the host's IPs and MAC addresses, so a
  license bound to the host's MAC works; `container.mac` and `[[extra_ports]]` have no effect (with a warning).
- **`bind_address`** can be an interface name in basebox; `up` looks up its IPv4 address (and
  prints it). With `0.0.0.0` SSH and VNC listen on every NIC, the fast ones included.
- **Ports** are the host's own: `up` checks that they are free, and `0` picks free ones. The ports are
  kept in a container label, so `basebox ps` shows them and other containers do not take them.
- **The VNC display `:1` must be free on the host**, because host networking also shares abstract unix
  sockets; `up` checks it.
- **`container.rdma`** (InfiniBand or RoCE) passes the `/dev/infiniband/uverbs*` nodes of the devices
  (and `umad*` when you may use them, and `rdma_cm`) and adds `IPC_LOCK` and an unlimited `memlock`.
  `up` fails with instructions if your hard memlock limit is not unlimited. Verbs programs
  (`ib_write_bw`, NCCL) work in either network mode; `rdma_cm` (`ib_write_bw -R`, IP-based MPI) needs
  `mode = "host"`. Add `rdma-core`, `ibverbs-utils`, `rdmacm-utils`, `perftest` and `infiniband-diags`
  (and `iperf3`, `ethtool`) to `packages`; the comment "Using an RDMA NIC" in the generated config
  lists every step.

## Development

```
src/casperbox/
├── cli.py             Typer commands
├── config.py          casperbox.toml parsing and validation
├── podman.py          podman build/run arguments and container queries
├── versions.py        toolflow versions: built-in (assets/versions) and plugins
├── plugins.py         `plugin new` / `plugin check`
├── templates/         casperbox.toml, basebox.toml and plugin README templates
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
| basebox container | for each tested OS: build and start a container, then check SSH password login, VNC, an extra forwarded port, a read-only mount, the MAC address, the host UID/GID, sudo and Firefox |
| casperbox container | the same for m2021a (with a stub Vivado), plus the conda env, `XILINX_PATH`, mlib_devel, device-tree-xlnx, Scilab and a writable `~/work` |

The container jobs only check that the environment comes up; toolflow functionality is not tested.
They run `tests/smoke/smoke.sh`, which also works on a host (needs `sshpass` and `curl`; the test
container is called `smoke-<tool>`, uses ports 22022/25901/28888 and is removed afterwards):

```bash
tests/smoke/smoke.sh basebox --os ubuntu:24.04
tests/smoke/smoke.sh casperbox
```

### Build files (`src/casperbox/assets/`)

```
common/packages/software.list   base apt packages, installed for every toolflow version
common/scripts/                 build scripts (install_pkg_list.sh installs a package list)
common/rootfs/                  sshd config, profile, entrypoint, default XFCE wallpaper
toolflow/Dockerfile             Dockerfile shared by all toolflow versions
versions/<version>/
    toolflow.toml               version definition (passed as build args)
    pkgs.list                   extra apt packages of this version (e.g. Vivado runtime deps); may be empty
    patch/*.sh                  optional: version-specific fixes, run as root at the end of the build
    Dockerfile                  optional: replaces toolflow/Dockerfile for this version
dev/                            basebox: Dockerfile, base.list, install scripts
```

Package lists have one apt package per line; `#` starts a comment and a trailing `?` marks an
optional package (installed only if the OS release has it). Packages are installed in this order:
`common/packages/software.list`, then `versions/<version>/pkgs.list`.

- Adding a package for every version: add it to `common/packages/software.list`.
- Adding a package for one version: add it to `versions/<version>/pkgs.list`.
- Fixing something for one version (e.g. m2021a preloads libudev for Vivado 2021.1 in `~/.bashrc`):
  add a script to `versions/<version>/patch/`. All `*.sh` there run with bash, as root, in file name
  order (`10-xxx.sh` before `50-yyy.sh`), after everything else is set up; a failing script fails the
  build. The package indexes are updated before, so a script may `apt-get install`. Only the last build
  step reruns when a patch changes.
- Vivado's `scripts/installLibs.sh`: add the packages it installs on Ubuntu to `pkgs.list` instead of
  running it (it ignores apt errors).
- Adding a toolflow version: create `versions/<new>/toolflow.toml` (copy m2021a's; `TOOLFLOW_VERSION`
  must match the directory name) and `pkgs.list` (may be empty). A Dockerfile is only needed if the
  version's build steps differ.

After any change, `casperbox up` rebuilds the image automatically.

For each build, the context is put together in a temporary directory: `common/`, `toolflow/` (or `dev/`)
and `versions/<version>/`, which comes from the package or from a plugin directory.

The shell-based tool (`casper.sh`) is available at tag `v0.1.0`.
