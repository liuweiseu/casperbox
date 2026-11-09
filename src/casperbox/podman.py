"""Thin wrapper around the podman CLI."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import Config, PortMap
from .errors import CasperError
from .versions import assets_dir

# Labels put on containers created by casperbox
LABEL_MANAGED = "casperbox.managed"
LABEL_CONFIG = "casperbox.config"
LABEL_TOOLFLOW = "casperbox.toolflow"

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


def build_args(cfg: Config, no_cache: bool = False) -> list[str]:
    args = {
        **cfg.toolflow.params,
        "CONTAINER_USER": cfg.user,
        "USER_UID": str(os.getuid()),
        "USER_GID": str(os.getgid()),
    }
    cmd = ["podman", "build", "-f", str(cfg.toolflow.dockerfile), "-t", cfg.toolflow.image]
    if no_cache:
        cmd.append("--no-cache")
    for k, v in args.items():
        cmd += ["--build-arg", f"{k}={v}"]
    cmd.append(str(assets_dir()))
    return cmd


def check_image_matches(cfg: Config) -> None:
    """The image user must match the config, otherwise mounted directories get wrong permissions."""
    labels = image_labels(cfg.toolflow.image)
    have = (labels.get("casper.user"), labels.get("casper.uid"), labels.get("casper.gid"))
    want = (cfg.user, str(os.getuid()), str(os.getgid()))
    if have != want:
        raise CasperError(
            f"User in image {cfg.toolflow.image} ({have[0]} {have[1]}:{have[2]}) does not match "
            f"the current config ({want[0]} {want[1]}:{want[2]}); please run: casperbox build"
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


def check_owner(cfg: Config, info: ContainerInfo) -> None:
    """Refuse to touch a container that was created from another config file."""
    if info.config_path != str(cfg.path):
        origin = info.config_path or "outside casperbox"
        raise CasperError(
            f"Container {cfg.name} was created by {origin}, not by {cfg.path}.\n"
            f"Change container.name in {cfg.path.name} to create a separate container."
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
            + f"\nChange them in {cfg.path.name}, or see `casperbox ps`."
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
    return {
        "VIVADO_DIR": str(cfg.vivado_dir),
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
        "--label", f"{LABEL_TOOLFLOW}={cfg.version}",
        "--network", pasta_network(cfg),
        # Map the host UID/GID into the container so mounted directories are writable
        "--userns", "keep-id",
        # The entrypoint needs root to set passwords and start sshd
        "--user", "root",
        # Do not SELinux-relabel mounted directories (avoid relabeling the Vivado install)
        "--security-opt", "label=disable",
        "--shm-size", cfg.shm_size,
        "--restart", "unless-stopped",
    ]
    for k in container_env(cfg):
        cmd += ["-e", k]
    volumes = [
        f"{cfg.vivado_dir}:{cfg.vivado_dir}:{cfg.vivado_mount}",
        f"{cfg.license_dir}:{cfg.home}/.Xilinx",
        f"{cfg.work_dir}:{cfg.home}/work",
        *(f"{m.host}:{m.container}:{m.mode}" for m in cfg.mounts),
    ]
    for v in volumes:
        cmd += ["-v", v]
    addr = f"[{cfg.bind_address}]" if ":" in cfg.bind_address else cfg.bind_address
    for p in cfg.all_ports:
        cmd += ["-p", f"{addr}:{p.host}:{p.container}/{p.protocol}"]
    cmd.append(cfg.toolflow.image)
    return cmd


def remove_container(name: str) -> None:
    run(["podman", "rm", "--force", "--time", "10", name])
