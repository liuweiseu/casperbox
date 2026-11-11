"""Thin wrapper around the podman CLI."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEV_TESTED_OS, RELEASE, TOOL_NAMES, Config, PortMap, cpuset_cores
from .errors import CasperError

# Labels put on containers created by casperbox
LABEL_MANAGED = "casperbox.managed"
LABEL_CONFIG = "casperbox.config"
LABEL_KIND = "casperbox.kind"  # release (casperbox) | dev (casperbox-dev)
LABEL_TOOLFLOW = "casperbox.toolflow"  # casperbox containers
LABEL_OS = "casperbox.os"  # casperbox-dev containers
# Label put on images: hash of the build inputs, used to detect outdated images
LABEL_BUILD_HASH = "casperbox.build-hash"
LABEL_SNAPSHOT_OF = "casperbox.snapshot-of"  # images made by `commit`: source container
LABEL_SOURCE = "casperbox.toolflow-source"  # toolflow images: built-in or the plugin directory

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
        args = {"BASE_IMAGE": cfg.base_image, "PACKAGES": " ".join(cfg.packages)}
    return {
        **args,
        "APT_MIRROR": cfg.apt_mirror,
        "CONTAINER_USER": cfg.user,
        "USER_UID": str(os.getuid()),
        "USER_GID": str(os.getgid()),
    }


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
    (assets/common plus toolflow/ and the version directory, or dev/)."""
    h = hashlib.sha256()
    for k, v in sorted(_build_arg_values(cfg).items()):
        h.update(f"arg {k}={v}\n".encode())
    for name, p in _source_files(cfg):
        h.update(f"file {name}\n".encode())
        h.update(p.read_bytes())
        h.update(b"\n")
    return h.hexdigest()


@contextmanager
def build_context(cfg: Config) -> Iterator[Path]:
    """Temporary build context: assets/common, toolflow/ (or dev/) and versions/<version>/,
    the latter from the package or from a plugin directory."""
    with tempfile.TemporaryDirectory(prefix="casperbox-build-") as tmp:
        root = Path(tmp)
        for name, src in cfg.build_sources:
            shutil.copytree(src, root / name, ignore=shutil.ignore_patterns("__pycache__"))
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
    """casperbox-dev: make sure the base image exists and is apt-based before building."""
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
            f"casperbox-dev supports Ubuntu / Debian images.\n{hint}"
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
        # containers created before casperbox-dev existed have no kind label
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
        result.append(
            ContainerInfo(
                name=names[0],
                state=c.get("State", ""),
                status=c.get("Status", ""),
                labels=c.get("Labels") or {},
                ports=_parse_ports(c.get("Ports")),
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


def check_owner(cfg: Config, info: ContainerInfo) -> None:
    """Refuse to touch a container that was created from another config file."""
    if info.config_path == str(cfg.path):
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
    # Name the container interface eth0 (pasta reuses the host interface name by default)
    net = "pasta:-I,eth0"
    if cfg.mac:
        net += f",--ns-mac-addr,{cfg.mac}"
    return net


def container_env(cfg: Config) -> dict[str, str]:
    env = {"VIVADO_DIR": str(cfg.vivado_dir)} if cfg.vivado_dir else {}
    return {
        **env,
        "CONTAINER_PWD": cfg.password,
        "VNC_PASSWORD": cfg.vnc_password,
        "VNC_GEOMETRY": cfg.vnc_geometry,
        "TZ": cfg.tz,
    }


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
        "--restart", "unless-stopped",
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
    for label in (LABEL_BUILD_HASH, LABEL_MANAGED, LABEL_CONFIG, LABEL_KIND):
        cmd += ["--change", f"LABEL {label}="]
    cmd += ["--change", f"LABEL {LABEL_SNAPSHOT_OF}={container}"]
    return [*cmd, container, image]
