"""Thin wrapper around the podman CLI."""

from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import socket
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    AUTO_PORT,
    AUTO_PORT_START,
    DEV_TESTED_OS,
    LEGACY_DEV_CONFIG_NAMES,
    RELEASE,
    SSH_CONTAINER_PORT,
    TOOL_NAMES,
    VNC_CONTAINER_PORT,
    Config,
    PortMap,
    cpuset_cores,
)
from .errors import CasperError

# Labels put on containers created by casperbox
LABEL_MANAGED = "casperbox.managed"
LABEL_CONFIG = "casperbox.config"
LABEL_KIND = "casperbox.kind"  # release (casperbox) | dev (basebox)
LABEL_TOOLFLOW = "casperbox.toolflow"  # casperbox containers
LABEL_OS = "casperbox.os"  # basebox containers
# Label put on images: hash of the build inputs, used to detect outdated images
LABEL_BUILD_HASH = "casperbox.build-hash"
LABEL_SNAPSHOT_OF = "casperbox.snapshot-of"  # images made by `commit`: source container
LABEL_SOURCE = "casperbox.toolflow-source"  # toolflow images: built-in or the plugin directory
# Host-network containers publish no ports, so their SSH / VNC host ports are kept in a label:
# "2222:22/tcp,5901:5901/tcp" (host:container/protocol, as for published ports)
LABEL_HOST_PORTS = "casperbox.host-ports"

GPU_NOFILE = 65535  # open-file limit of containers with GPUs
SYS_INFINIBAND = Path("/sys/class/infiniband")
DEV_INFINIBAND = Path("/dev/infiniband")
SIOCGIFADDR = 0x8915  # Linux ioctl: IPv4 address of an interface

# Environment passed to the container. Values are put in podman's own environment
# and referenced by name (-e NAME), so passwords never appear on a command line.
SECRET_ENV = ("CONTAINER_PWD", "VNC_PASSWORD")


def check_dependencies() -> None:
    if os.getuid() == 0:
        raise CasperError("Do not run as root; use a regular user with rootless podman")
    if not shutil.which("podman"):
        raise CasperError("podman not found, please install podman (>= 4.4)")
    if not shutil.which("pasta"):
        raise CasperError("pasta not found; please install the passt package (the container uses pasta networking)")
    rootless = _output(["podman", "info", "--format", "{{.Host.Security.Rootless}}"], check=False).strip()
    if rootless != "true":
        raise CasperError("podman is not running in rootless mode; run casperbox as a regular user")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _output(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise CasperError(f"{' '.join(cmd[:3])} failed: {r.stderr.strip()}")
    return r.stdout if r.returncode == 0 else ""


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    r = subprocess.run(cmd, env={**os.environ, **env} if env else None, check=False)
    if r.returncode != 0:
        raise CasperError(f"{' '.join(cmd[:2])} failed (exit code {r.returncode})")


def terminal_env() -> list[str]:
    """`-e` options passing the host terminal type into `podman exec`.

    podman sets TERM=xterm by default, and Ubuntu's ~/.bashrc only enables the colour
    prompt for xterm-color / *-256color terminals, so pass the host's TERM instead.
    """
    term = os.environ.get("TERM", "")
    if not term or term == "dumb":
        term = "xterm-256color"
    args = ["-e", f"TERM={term}"]
    if os.environ.get("COLORTERM"):
        args += ["-e", f"COLORTERM={os.environ['COLORTERM']}"]
    return args


def exec_(cmd: list[str]) -> None:
    """Replace the current process (for interactive commands)."""
    os.execvp(cmd[0], cmd)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def image_exists(image: str) -> bool:
    return subprocess.run(["podman", "image", "exists", image], check=False).returncode == 0


def image_labels(image: str) -> dict[str, str]:
    out = _output(["podman", "image", "inspect", "--format", "{{json .Labels}}", image], check=False)
    if not out.strip():
        return {}
    return json.loads(out) or {}


def _build_arg_values(cfg: Config) -> dict[str, str]:
    if cfg.kind == RELEASE:
        args = dict(cfg.toolflow.params)
    else:
        args = {
            "BASE_IMAGE": cfg.base_image,
            "PACKAGES": " ".join(cfg.packages),
            "APT_REPOS": " ".join(r.build_arg for r in cfg.apt_repos),
        }
    return {
        **args,
        "APT_MIRROR": cfg.apt_mirror,
        "CONTAINER_USER": cfg.user,
        "USER_UID": str(os.getuid()),
        "USER_GID": str(os.getgid()),
    }


def tool_files(cfg: Config) -> dict[str, bytes]:
    """basebox [[tools]] as files of the build context: tools/<root|user>/NN-<name>.sh and .version.

    NN is the position in the config, so scripts run in the order written there. Toolflow images
    have no tools (and no tools/ directory).
    """
    if cfg.kind == RELEASE:
        return {}
    files: dict[str, bytes] = {"tools/root/.keep": b"", "tools/user/.keep": b""}  # COPY needs both directories
    for i, t in enumerate(cfg.tools):
        base = f"tools/{t.run_as}/{i:02d}-{t.name}"
        files[f"{base}.sh"] = f"#!/usr/bin/env bash\nset -euo pipefail\n{t.script}".encode()
        files[f"{base}.version"] = t.version.encode()
    return files


def _source_files(cfg: Config) -> list[tuple[str, Path]]:
    """(path in the build context, file) for every file the image is built from."""
    out = []
    for name, src in cfg.build_sources:
        if not src.is_dir():
            continue
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                out.append((f"{name}/{p.relative_to(src).as_posix()}", p))
    return out


def build_hash(cfg: Config) -> str:
    """Hash of everything the image is built from: build args and all files of the build context
    (assets/common plus toolflow/ and the version directory, or dev/ and the [[tools]] scripts)."""
    h = hashlib.sha256()
    for k, v in sorted(_build_arg_values(cfg).items()):
        h.update(f"arg {k}={v}\n".encode())
    for name, p in _source_files(cfg):
        h.update(f"file {name}\n".encode())
        h.update(p.read_bytes())
        h.update(b"\n")
    for name, data in sorted(tool_files(cfg).items()):
        h.update(f"tool {name}\n".encode())
        h.update(data)
        h.update(b"\n")
    return h.hexdigest()


@contextmanager
def build_context(cfg: Config) -> Iterator[Path]:
    """Temporary build context: assets/common, toolflow/ and versions/<version>/ (from the package
    or from a plugin directory), or for basebox: common/, dev/ and the generated tools/."""
    with tempfile.TemporaryDirectory(prefix="casperbox-build-") as tmp:
        root = Path(tmp)
        for name, src in cfg.build_sources:
            shutil.copytree(src, root / name, ignore=shutil.ignore_patterns("__pycache__"))
        for name, data in tool_files(cfg).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        yield root


def build_args(cfg: Config, context: Path, no_cache: bool = False) -> list[str]:
    cmd = [
        "podman", "build", "-f", str(context / cfg.dockerfile_in_context), "-t", cfg.image,
        "--label", f"{LABEL_BUILD_HASH}={build_hash(cfg)}",
    ]
    if cfg.toolflow:
        cmd += ["--label", f"{LABEL_SOURCE}={cfg.toolflow.source}"]
    if no_cache:
        cmd.append("--no-cache")
    for k, v in _build_arg_values(cfg).items():
        cmd += ["--build-arg", f"{k}={v}"]
    cmd.append(str(context))
    return cmd


def image_state(cfg: Config) -> str:
    """'missing', 'outdated' or 'current'."""
    if not image_exists(cfg.image):
        return "missing"
    if image_labels(cfg.image).get(LABEL_BUILD_HASH) != build_hash(cfg):
        return "outdated"
    return "current"


def check_image_matches(cfg: Config) -> None:
    """The image user must match the config, otherwise mounted directories get wrong permissions."""
    labels = image_labels(cfg.image)
    have = (labels.get("casper.user"), labels.get("casper.uid"), labels.get("casper.gid"))
    want = (cfg.user, str(os.getuid()), str(os.getgid()))
    if have != want:
        raise CasperError(
            f"User in image {cfg.image} ({have[0]} {have[1]}:{have[2]}) does not match "
            f"the current config ({want[0]} {want[1]}:{want[2]}); please run: {cfg.tool} build"
        )


def check_base_image(cfg: Config) -> None:
    """basebox: make sure the base image exists and is apt-based before building."""
    img = cfg.base_image
    hint = f"Check `os` in {cfg.path.name}; tested: {', '.join(DEV_TESTED_OS)}"
    if not image_exists(img) and subprocess.run(["podman", "pull", img], check=False).returncode != 0:
        raise CasperError(f"Base image {img} could not be pulled (see the error above).\n{hint}")
    r = subprocess.run(
        ["podman", "run", "--rm", "--network", "none", "--entrypoint", "sh", img, "-c", "command -v apt-get"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise CasperError(
            f"Base image {img} is not an apt-based system (apt-get not found); "
            f"basebox supports Ubuntu / Debian images.\n{hint}"
        )


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

@dataclass
class ContainerInfo:
    name: str
    state: str
    status: str
    labels: dict[str, str]
    ports: list[PortMap]

    @property
    def config_path(self) -> str:
        return self.labels.get(LABEL_CONFIG, "")

    @property
    def kind(self) -> str:
        # containers created before basebox existed have no kind label
        return self.labels.get(LABEL_KIND, RELEASE)


def _parse_ports(raw: Any) -> list[PortMap]:
    ports: list[PortMap] = []
    for p in raw or []:
        try:
            for i in range(int(p.get("range", 1) or 1)):
                ports.append(PortMap(int(p["host_port"]) + i, int(p["container_port"]) + i, p.get("protocol", "tcp")))
        except (KeyError, TypeError, ValueError):
            continue
    return ports


def _label_ports(value: str) -> list[PortMap]:
    """Ports of LABEL_HOST_PORTS: "2222:22/tcp,5901:5901/tcp"."""
    ports: list[PortMap] = []
    for item in filter(None, value.split(",")):
        try:
            host, _, rest = item.partition(":")
            cport, _, proto = rest.partition("/")
            ports.append(PortMap(int(host), int(cport), proto or "tcp"))
        except ValueError:
            continue
    return ports


def list_containers(name: str | None = None) -> list[ContainerInfo]:
    cmd = ["podman", "ps", "-a", "--format", "json"]
    if name:
        cmd += ["--filter", f"name=^{name}$"]
    else:
        cmd += ["--filter", f"label={LABEL_MANAGED}=true"]
    out = _output(cmd)
    result = []
    for c in json.loads(out or "[]"):
        names = c.get("Names") or [""]
        labels = c.get("Labels") or {}
        result.append(
            ContainerInfo(
                name=names[0],
                state=c.get("State", ""),
                status=c.get("Status", ""),
                labels=labels,
                # host-network containers publish nothing; their ports are in a label
                ports=_parse_ports(c.get("Ports")) or _label_ports(labels.get(LABEL_HOST_PORTS, "")),
            )
        )
    return result


def get_container(name: str) -> ContainerInfo | None:
    found = [c for c in list_containers(name) if c.name == name]
    return found[0] if found else None


@dataclass(frozen=True)
class MountInfo:
    source: str
    destination: str
    mode: str  # ro | rw


def container_mounts(name: str) -> list[MountInfo]:
    """Mounts of an existing container, as podman reports them."""
    out = _output(["podman", "container", "inspect", "--format", "{{json .Mounts}}", name], check=False)
    if not out.strip():
        return []
    mounts = []
    for m in json.loads(out) or []:
        try:
            mounts.append(MountInfo(m["Source"], m["Destination"], "rw" if m.get("RW", True) else "ro"))
        except (KeyError, TypeError):
            continue
    return mounts


def container_limits(name: str) -> dict[str, str]:
    """Resource limits of an existing container (only the ones that are set)."""
    fmt = "{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.CpusetCpus}}"
    out = _output(["podman", "container", "inspect", "--format", fmt, name], check=False).strip()
    parts = out.split("|")
    if len(parts) != 3:
        return {}
    limits = {}
    try:
        if int(parts[0] or 0):
            limits["memory"] = f"{int(parts[0]) / 2**30:g}g"
        if int(parts[1] or 0):
            limits["cpus"] = f"{int(parts[1]) / 1e9:g}"
    except ValueError:
        pass
    if parts[2]:
        limits["cpuset"] = parts[2]
    return limits


def _container_devices(name: str) -> list[str]:
    out = _output(["podman", "container", "inspect", "--format", "{{json .HostConfig.Devices}}", name], check=False)
    try:
        devices = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        return []
    return [str(d.get("PathOnHost", "")) for d in devices or [] if isinstance(d, dict)]


def container_gpus(name: str) -> list[str]:
    """CDI devices (nvidia.com/gpu=N) of an existing container."""
    return [d for d in _container_devices(name) if d.startswith("nvidia.com/gpu=")]


def container_rdma(name: str) -> list[str]:
    """RDMA device nodes of an existing container."""
    return [d for d in _container_devices(name) if d.startswith("/dev/infiniband/")]


def container_network(name: str) -> str:
    return _output(["podman", "container", "inspect", "--format", "{{.HostConfig.NetworkMode}}", name],
                   check=False).strip()


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

def limit_args(cfg: Config) -> list[str]:
    """podman run options for container.memory / cpus / cpuset (none when they are not set)."""
    args = []
    if cfg.memory:
        # Same value for --memory-swap: no swap on top of the limit, which would slow the whole host down
        args += ["--memory", cfg.memory, "--memory-swap", cfg.memory]
    if cfg.cpus:
        args += ["--cpus", f"{cfg.cpus:g}"]
    if cfg.cpuset:
        args += ["--cpuset-cpus", cfg.cpuset]
    return args


# config key -> cgroup controller it needs
LIMIT_CONTROLLERS = {"memory": "memory", "cpus": "cpu", "cpuset": "cpuset"}

DELEGATE_HELP = """Ask the administrator to delegate the cgroup controllers to regular users (once per host):
  sudo mkdir -p /etc/systemd/system/user@.service.d
  printf '[Service]\\nDelegate=cpu cpuset io memory pids\\n' | sudo tee /etc/systemd/system/user@.service.d/delegate.conf
  sudo systemctl daemon-reload
then log out and in again (all your sessions). Or remove the limit from the config."""


def check_limits(cfg: Config) -> None:
    """Check that the host can apply the configured resource limits (does nothing without limits)."""
    keys = [k for k in LIMIT_CONTROLLERS if getattr(cfg, k)]
    if not keys:
        return
    names = ", ".join(f"container.{k}" for k in keys)
    version = _output(["podman", "info", "--format", "{{.Host.CgroupsVersion}}"], check=False).strip()
    if version != "v2":
        raise CasperError(
            f"{names} need cgroup v2, but this host uses cgroup {version or '(unknown)'}: rootless podman "
            "cannot limit resources on it. Remove the limit from the config, or switch the host to cgroup v2."
        )
    raw = _output(["podman", "info", "--format", "{{json .Host.CgroupControllers}}"], check=False).strip()
    try:
        available = set(json.loads(raw) or []) if raw else set()
    except json.JSONDecodeError:
        available = set()
    missing = [k for k in keys if LIMIT_CONTROLLERS[k] not in available]
    if missing:
        controllers = ", ".join(LIMIT_CONTROLLERS[k] for k in missing)
        raise CasperError(
            f"{', '.join(f'container.{k}' for k in missing)} need the cgroup controller(s) {controllers}, "
            f"which are not delegated to your user (available: {', '.join(sorted(available)) or 'none'}).\n"
            + DELEGATE_HELP
        )
    ncpu = os.cpu_count() or 0
    if ncpu and cfg.cpus > ncpu:
        raise CasperError(f"container.cpus = {cfg.cpus:g}, but the host has only {ncpu} CPUs")
    if ncpu and cfg.cpuset and max(cpuset_cores(cfg.cpuset)) >= ncpu:
        raise CasperError(f"container.cpuset = {cfg.cpuset!r}, but the host has only CPUs 0-{ncpu - 1}")


# ---------------------------------------------------------------------------
# GPUs, RDMA and host networking (basebox)
# ---------------------------------------------------------------------------

def gpu_args(cfg: Config) -> list[str]:
    """podman run options for container.gpus: one CDI device per GPU, and a higher file limit."""
    args: list[str] = []
    for g in cfg.gpus:
        args += ["--device", f"nvidia.com/gpu={g}"]
    if cfg.gpus:
        args += ["--ulimit", f"nofile={GPU_NOFILE}:{GPU_NOFILE}"]
    return args


def check_gpus(cfg: Config) -> None:
    """Check that every GPU of container.gpus is a CDI device on this host (does nothing without GPUs)."""
    if not cfg.gpus:
        return
    if not shutil.which("nvidia-ctk"):
        raise CasperError(
            f"container.gpus = {','.join(cfg.gpus)!r}, but nvidia-ctk is not installed.\n"
            "  Ask the administrator to install nvidia-container-toolkit on the host, then:\n"
            "    sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
        )
    listed = set(_output(["nvidia-ctk", "cdi", "list"], check=False).split())
    missing = [g for g in cfg.gpus if f"nvidia.com/gpu={g}" not in listed]
    if missing:
        raise CasperError(
            f"CDI device(s) not found: {', '.join(f'nvidia.com/gpu={g}' for g in missing)}\n"
            f"  Available: {' '.join(sorted(d for d in listed if d.startswith('nvidia.com/'))) or 'none'}\n"
            "  Generate the spec (and again after every driver upgrade):\n"
            "    sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
        )


def interface_address(name: str) -> str:
    """IPv4 address of a network interface of this host."""
    import fcntl  # Unix only

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            packed = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", name.encode()[:15]))
        except OSError:
            known = ", ".join(n for _, n in socket.if_nameindex()) or "none"
            raise CasperError(
                f"ports.bind_address = {name!r}: no such network interface, or it has no IPv4 address "
                f"(interfaces: {known})"
            ) from None
    return socket.inet_ntoa(packed[20:24])


def resolve_bind_address(cfg: Config) -> None:
    """Replace an interface name in ports.bind_address by the interface's current IPv4 address."""
    if cfg.bind_name:
        cfg.bind_address = interface_address(cfg.bind_name)


def check_host_display(cfg: Config, own: ContainerInfo | None) -> None:
    """Host network mode shares abstract unix sockets with the host: the VNC display :1 must be free."""
    if not cfg.host_network or own:  # an existing container is removed before the new one starts
        return
    try:
        unix = Path("/proc/net/unix").read_text(errors="replace")
    except OSError:
        return
    if any(line.rstrip().endswith("@/tmp/.X11-unix/X1") for line in unix.splitlines()):
        raise CasperError(
            'network.mode = "host": the X display :1 is already in use on this host (an X server or VNC '
            "server holds @/tmp/.X11-unix/X1), and the container would share it.\n"
            'Stop that server, or use network.mode = "pasta".'
        )


def rdma_devices(names: list[str], sys_root: Path = SYS_INFINIBAND, dev_root: Path = DEV_INFINIBAND) -> list[str]:
    """/dev/infiniband nodes for RDMA devices (["all"] = every device): uverbs, umad (if usable) and rdma_cm."""
    if not names:
        return []
    available = sorted(p.name for p in sys_root.iterdir()) if sys_root.is_dir() else []
    if not available:
        raise CasperError(
            "container.rdma is set, but this host has no RDMA devices (/sys/class/infiniband is empty or missing). "
            "Check the driver (MLNX_OFED or the inbox mlx5_ib module) with `ibv_devices`."
        )
    wanted = available if names == ["all"] else names
    missing = [n for n in wanted if n not in available]
    if missing:
        raise CasperError(f"RDMA device(s) not found: {', '.join(missing)} (available: {', '.join(available)})")
    nodes: list[str] = []
    for dev in wanted:
        for sub, required in (("infiniband_verbs", True), ("infiniband_mad", False)):
            d = sys_root / dev / "device" / sub
            found = sorted(p.name for p in d.iterdir()) if d.is_dir() else []
            if required and not found:
                raise CasperError(f"RDMA device {dev} has no uverbs node (is ib_uverbs loaded?)")
            for node in found:
                path = dev_root / node
                # the management (umad) nodes are often root-only; the verbs nodes are what applications need
                if required or os.access(path, os.R_OK | os.W_OK):
                    nodes.append(str(path))
    cm = dev_root / "rdma_cm"
    if cm.exists():
        nodes.append(str(cm))
    return list(dict.fromkeys(nodes))


def check_memlock() -> None:
    """RDMA pins memory: the hard memlock limit of this user must be unlimited."""
    _, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if hard != resource.RLIM_INFINITY:
        raise CasperError(
            f"container.rdma needs an unlimited locked-memory limit, but yours is {hard} bytes.\n"
            "Ask the administrator (once per host):\n"
            "  printf '* soft memlock unlimited\\n* hard memlock unlimited\\n' | "
            "sudo tee /etc/security/limits.d/90-rdma.conf\n"
            "then log out and in again (all your sessions)."
        )


def resolve_host_settings(cfg: Config) -> None:
    """Look up what depends on this host: the IP of ports.bind_address, the RDMA device nodes."""
    resolve_bind_address(cfg)
    if cfg.rdma:
        check_memlock()
        cfg.rdma_paths = rdma_devices(cfg.rdma)


def rdma_args(cfg: Config) -> list[str]:
    """podman run options for container.rdma: the device nodes, and what RDMA needs to pin memory."""
    if not cfg.rdma:
        return []
    args: list[str] = []
    for path in cfg.rdma_paths:
        args += ["--device", path]
    return [*args, "--cap-add", "IPC_LOCK", "--ulimit", "memlock=-1:-1"]


def check_owner(cfg: Config, info: ContainerInfo) -> None:
    """Refuse to touch a container that was created from another config file.

    A basebox config renamed from an old default name (casperbox-dev.toml, casper-dev.toml) to the
    current one in the same directory still owns its container; recreating it updates the label.
    """
    if info.config_path == str(cfg.path):
        return
    old = Path(info.config_path)
    if (cfg.kind == info.kind and old.parent == cfg.path.parent and old.name in LEGACY_DEV_CONFIG_NAMES
            and not old.exists()):
        return
    if not info.config_path:
        raise CasperError(
            f"A container named {cfg.name} already exists and was not created by casperbox.\n"
            f"Change container.name in {cfg.path.name}, or remove that container with: podman rm -f {cfg.name}"
        )
    owner_tool = TOOL_NAMES.get(info.kind, "casperbox")
    raise CasperError(
        f"Container {cfg.name} was created by {info.config_path}, not by {cfg.path}.\n"
        f"Change container.name in {cfg.path.name} to create a separate container "
        f"(`{owner_tool} ps` lists containers and their config files)."
    )


def check_ports_free(cfg: Config, own: ContainerInfo | None) -> None:
    """Check all host ports at once; report every port held by another process or container."""
    held_by_own = {(p.host, p.protocol) for p in own.ports} if own else set()
    others = {
        (p.host, p.protocol): c.name
        for c in list_containers()
        if c.state == "running" and (own is None or c.name != own.name)
        for p in c.ports
    }
    wanted = [("ports.ssh", cfg.all_ports[0]), ("ports.vnc", cfg.all_ports[1])]
    if not cfg.host_network:
        wanted += [(f"extra_ports[{i}]", pm) for i, pm in enumerate(cfg.extra_ports)]

    problems = []
    for key_name, pm in wanted:
        key = (pm.host, pm.protocol)
        where = f"{pm.host}/{pm.protocol} ({key_name})"
        if key in others:
            problems.append(f"{where}: used by container {others[key]}")
        elif key not in held_by_own and not _port_free(cfg.bind_address, pm.host, pm.protocol):
            problems.append(f"{where}: already in use on {cfg.bind_address}")
    if problems:
        raise CasperError(
            f"{len(problems)} host port(s) unavailable:\n  " + "\n  ".join(problems)
            + f"\nChange them in {cfg.path.name}, or see `{cfg.tool} ps`."
        )


def pick_auto_ports(cfg: Config, own: ContainerInfo | None) -> dict[str, int]:
    """Host ports for ports.ssh / ports.vnc set to 0: the first free one from AUTO_PORT_START.

    The existing container of this config keeps its port. Ports of other casperbox containers
    count as used even when those are stopped (they need them again at start); for host-network
    containers they come from LABEL_HOST_PORTS.
    """
    if AUTO_PORT not in (cfg.ssh_port, cfg.vnc_port):
        return {}
    held_by_own = {p.container: p.host for p in own.ports if p.protocol == "tcp"} if own else {}
    used = {p.host for c in list_containers() if own is None or c.name != own.name
            for p in c.ports if p.protocol == "tcp"}
    if not cfg.host_network:
        used |= {p.host for p in cfg.extra_ports if p.protocol == "tcp"}
    used |= {p for p in (cfg.ssh_port, cfg.vnc_port) if p != AUTO_PORT}
    picked: dict[str, int] = {}
    for key, cport in (("ssh", SSH_CONTAINER_PORT), ("vnc", VNC_CONTAINER_PORT)):
        if getattr(cfg, f"{key}_port") != AUTO_PORT:
            continue
        port = held_by_own.get(cport)
        if port is None or port in used:
            port = next((p for p in range(AUTO_PORT_START[key], 65536)
                         if p not in used and _port_free(cfg.bind_address, p, "tcp")), None)
        if port is None:
            raise CasperError(f"No free host port found for ports.{key} from {AUTO_PORT_START[key]}")
        picked[key] = port
        used.add(port)
    return picked


def _port_free(addr: str, port: int, proto: str) -> bool:
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    kind = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    with socket.socket(family, kind) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((addr, port))
        except OSError:
            return False
    return True


def pasta_network(cfg: Config) -> str:
    if cfg.host_network:
        return "host"
    # Name the container interface eth0 (pasta reuses the host interface name by default)
    net = "pasta:-I,eth0"
    if cfg.mac:
        net += f",--ns-mac-addr,{cfg.mac}"
    return net


def container_env(cfg: Config) -> dict[str, str]:
    env = {"VIVADO_DIR": str(cfg.vivado_dir)} if cfg.vivado_dir else {}
    env |= {
        "CONTAINER_PWD": cfg.password,
        "VNC_PASSWORD": cfg.vnc_password,
        "VNC_GEOMETRY": cfg.vnc_geometry,
        "TZ": cfg.tz,
    }
    if cfg.host_network:
        # SSH and VNC listen on the host's ports themselves, on ports.bind_address only
        env |= {
            "CASPERBOX_SSH_PORT": str(cfg.ssh_port),
            "CASPERBOX_VNC_PORT": str(cfg.vnc_port),
            "CASPERBOX_BIND": cfg.bind_address,
        }
    return env


def run_args(cfg: Config) -> list[str]:
    cmd = [
        "podman", "run", "-d",
        "--name", cfg.name,
        "--hostname", cfg.name,
        "--label", f"{LABEL_MANAGED}=true",
        "--label", f"{LABEL_CONFIG}={cfg.path}",
        "--label", f"{LABEL_KIND}={cfg.kind}",
        *(["--label", f"{LABEL_TOOLFLOW}={cfg.version}"] if cfg.kind == RELEASE else ["--label", f"{LABEL_OS}={cfg.os}"]),
        "--network", pasta_network(cfg),
        # Map the host UID/GID into the container so mounted directories are writable
        "--userns", "keep-id",
        # The entrypoint needs root to set passwords and start sshd
        "--user", "root",
        # Do not SELinux-relabel mounted directories (avoid relabeling the Vivado install)
        "--security-opt", "label=disable",
        "--shm-size", cfg.shm_size,
        *limit_args(cfg),
        *gpu_args(cfg),
        *rdma_args(cfg),
        "--restart", "unless-stopped",
    ]
    if cfg.host_network:
        cmd[cmd.index("--network"):cmd.index("--network")] = [
            "--label", f"{LABEL_HOST_PORTS}=" + ",".join(f"{p.host}:{p.container}/{p.protocol}" for p in cfg.all_ports)
        ]
    for k in container_env(cfg):
        cmd += ["-e", k]
    volumes = []
    if cfg.kind == RELEASE:
        volumes += [
            f"{cfg.vivado_dir}:{cfg.vivado_dir}:{cfg.vivado_mount}",
            f"{cfg.license_dir}:{cfg.home}/.Xilinx",
            f"{cfg.work_dir}:{cfg.home}/work",
        ]
    volumes += [f"{m.host}:{m.container}:{m.mode}" for m in cfg.mounts]
    for v in volumes:
        cmd += ["-v", v]
    if not cfg.host_network:
        addr = f"[{cfg.bind_address}]" if ":" in cfg.bind_address else cfg.bind_address
        for p in cfg.all_ports:
            cmd += ["-p", f"{addr}:{p.host}:{p.container}/{p.protocol}"]
    cmd.append(cfg.image)
    return cmd


def remove_container(name: str) -> None:
    run(["podman", "rm", "--force", "--time", "10", name])


def commit_args(container: str, image: str) -> list[str]:
    """`podman commit` of a container into an image.

    The casperbox labels are cleared: the snapshot is not an image casperbox built (so `up`
    never mistakes it for an up-to-date build), and containers started from it with
    `podman run` must not show up in `casperbox ps` as managed containers.
    """
    cmd = ["podman", "commit"]
    for label in (LABEL_BUILD_HASH, LABEL_MANAGED, LABEL_CONFIG, LABEL_KIND, LABEL_HOST_PORTS):
        cmd += ["--change", f"LABEL {label}="]
    cmd += ["--change", f"LABEL {LABEL_SNAPSHOT_OF}={container}"]
    return [*cmd, container, image]
