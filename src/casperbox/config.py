"""casperbox.toml: load, apply defaults, validate."""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

from .errors import CasperError
from .versions import Toolflow, load_toolflow

DEFAULT_CONFIG_NAME = "casperbox.toml"

# Container ports used by the image itself
SSH_CONTAINER_PORT = 22
VNC_CONTAINER_PORT = 5901


@dataclass(frozen=True)
class Mount:
    host: Path
    container: str
    mode: str  # ro | rw


@dataclass(frozen=True)
class PortMap:
    host: int
    container: int
    protocol: str  # tcp | udp


@dataclass
class Config:
    path: Path
    toolflow: Toolflow
    name: str
    user: str
    password: str
    vnc_password: str
    mac: str
    shm_size: str
    tz: str
    vivado_dir: Path
    vivado_mount: str
    license_dir: Path
    work_dir: Path
    bind_address: str
    ssh_port: int
    vnc_port: int
    vnc_geometry: str
    mounts: list[Mount] = field(default_factory=list)
    extra_ports: list[PortMap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.toolflow.version

    @property
    def home(self) -> str:
        return f"/home/{self.user}"

    @property
    def all_ports(self) -> list[PortMap]:
        return [
            PortMap(self.ssh_port, SSH_CONTAINER_PORT, "tcp"),
            PortMap(self.vnc_port, VNC_CONTAINER_PORT, "tcp"),
            *self.extra_ports,
        ]


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def render_template(toolflow: Toolflow) -> str:
    from importlib.resources import files

    text = (files("casperbox") / "templates" / "casperbox.toml").read_text(encoding="utf-8")
    return Template(text).substitute(version=toolflow.version, vivado_version=toolflow.vivado_version)


# ---------------------------------------------------------------------------
# Typed access to the TOML document
# ---------------------------------------------------------------------------

class _Table:
    """Reads keys from one TOML table, checking types and rejecting unknown keys."""

    def __init__(self, data: Any, where: str):
        if not isinstance(data, dict):
            raise CasperError(f"{where} must be a table")
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def _key(self, key: str) -> str:
        return f"{self.where}.{key}" if self.where else key

    def get(self, key: str, typ: type, default: Any = ...) -> Any:
        self.used.add(key)
        if key not in self.data:
            if default is ...:
                raise CasperError(f"{self._key(key)} is required")
            return default
        value = self.data[key]
        # bool is a subclass of int in Python; do not accept true/false as a port
        if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
            raise CasperError(f"{self._key(key)} must be a {typ.__name__}, got {value!r}")
        return value

    def table(self, key: str) -> _Table:
        self.used.add(key)
        return _Table(self.data.get(key, {}), self._key(key))

    def array_of_tables(self, key: str) -> list[_Table]:
        self.used.add(key)
        items = self.data.get(key, [])
        if not isinstance(items, list):
            raise CasperError(f"{self._key(key)} must be an array of tables ([[{key}]])")
        return [_Table(item, f"{self._key(key)}[{i}]") for i, item in enumerate(items)]

    def finish(self) -> None:
        unknown = sorted(set(self.data) - self.used)
        if unknown:
            raise CasperError(f"Unknown key(s) in {self.where or 'top level'}: {', '.join(unknown)}")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_GEOMETRY_RE = re.compile(r"^[0-9]+x[0-9]+$")
_SHM_RE = re.compile(r"^[0-9]+[bkmgBKMG]?$")


def _host_path(value: str, base: Path) -> Path:
    """Expand ~ and resolve relative paths against the config file's directory."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _check_port(value: int, what: str) -> int:
    if not 1 <= value <= 65535:
        raise CasperError(f"{what} is out of range (1-65535): {value}")
    return value


def _check_no_newline(value: str, what: str) -> str:
    if "\n" in value or "\r" in value or "\0" in value:
        raise CasperError(f"{what} must not contain newlines")
    return value


def normalize_mac(mac: str) -> str:
    mac = mac.strip().lower().replace("-", ":")
    if not _MAC_RE.match(mac):
        raise CasperError(f"Invalid container.mac: {mac!r} (expected xx:xx:xx:xx:xx:xx)")
    if int(mac[:2], 16) & 1:
        raise CasperError(f"container.mac must not be a multicast address: {mac}")
    if mac == "00:00:00:00:00:00":
        raise CasperError("container.mac must not be all zeros")
    return mac


def _protected_paths(user: str) -> list[str]:
    home = f"/home/{user}"
    return [
        "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root", "/run",
        "/sbin", "/sys", "/tmp", "/usr", "/var",
        "/opt/conda", "/opt/scilab", "/opt/casper",
        home, f"{home}/work", f"{home}/.Xilinx", f"{home}/toolflow", f"{home}/.local",
    ]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def resolve_config_path(path: Path | None) -> Path:
    p = (path or Path(DEFAULT_CONFIG_NAME)).expanduser()
    p = p if p.is_absolute() else Path.cwd() / p
    if not p.is_file():
        if path is None:
            raise CasperError(
                f"{DEFAULT_CONFIG_NAME} not found in {Path.cwd()}; "
                "run `casperbox init` to create one, or pass -c FILE"
            )
        raise CasperError(f"Config file not found: {p}")
    return p.resolve()


def load_config(path: Path | None = None) -> Config:
    cfg_path = resolve_config_path(path)
    try:
        with cfg_path.open("rb") as f:
            doc = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise CasperError(f"{cfg_path}: {e}") from e
    return parse_config(doc, cfg_path)


def parse_config(doc: dict[str, Any], cfg_path: Path) -> Config:
    base = cfg_path.parent
    top = _Table(doc, "")
    warnings: list[str] = []

    toolflow = load_toolflow(top.get("version", str))

    # ---- [container] ----
    c = top.table("container")
    name = c.get("name", str, f"casper-{toolflow.version}")
    if not _NAME_RE.match(name):
        raise CasperError(f"Invalid container.name: {name!r}")
    user = c.get("user", str, "casper")
    if not _USER_RE.match(user) or user == "root":
        raise CasperError(f"Invalid container.user: {user!r}")
    password = _check_no_newline(c.get("password", str, "casper"), "container.password")
    if not password:
        raise CasperError("container.password must not be empty")
    vnc_password = _check_no_newline(c.get("vnc_password", str, password), "container.vnc_password")
    if len(vnc_password) < 6:
        raise CasperError("container.vnc_password must be at least 6 characters")
    if len(vnc_password) > 8:
        warnings.append("container.vnc_password is longer than 8 characters; only the first 8 are used")
    mac = c.get("mac", str, "")
    mac = normalize_mac(mac) if mac.strip() else ""
    shm_size = c.get("shm_size", str, "2g")
    if not _SHM_RE.match(shm_size):
        raise CasperError(f"Invalid container.shm_size: {shm_size!r}")
    tz = _check_no_newline(c.get("tz", str, "UTC"), "container.tz")
    c.finish()

    # ---- [vivado] ----
    v = top.table("vivado")
    vivado_dir = _host_path(v.get("dir", str), base)
    vivado_mount = v.get("mount", str, "ro")
    if vivado_mount not in ("ro", "rw"):
        raise CasperError("vivado.mount must be 'ro' or 'rw'")
    v.finish()
    if not vivado_dir.is_dir():
        raise CasperError(f"vivado.dir does not exist: {vivado_dir}")
    vv = toolflow.vivado_version
    if not (vivado_dir / "Vivado" / vv / "settings64.sh").is_file() and not (vivado_dir / "settings64.sh").is_file():
        warnings.append(
            f"Neither Vivado/{vv}/settings64.sh nor settings64.sh found under {vivado_dir}; "
            f"toolflow {toolflow.version} requires Vivado {vv}"
        )
    # Vivado is mounted at the same path; it must not shadow system or toolflow directories
    for protected in _protected_paths(user):
        if protected == str(vivado_dir) or protected.startswith(f"{vivado_dir}/") or str(vivado_dir) == "/":
            raise CasperError(f"vivado.dir={vivado_dir} would shadow {protected} in the container, please use another path")

    # ---- [paths] ----
    p = top.table("paths")
    license_dir = _host_path(p.get("license_dir", str, "~/.Xilinx"), base)
    work_dir = _host_path(p.get("work_dir", str, "./work"), base)
    p.finish()
    if not license_dir.is_dir():
        raise CasperError(f"paths.license_dir does not exist: {license_dir}")
    if work_dir.exists() and not work_dir.is_dir():
        raise CasperError(f"paths.work_dir is not a directory: {work_dir}")

    # ---- [ports] ----
    pt = top.table("ports")
    bind_address = pt.get("bind_address", str, "0.0.0.0")
    try:
        ipaddress.ip_address(bind_address)
    except ValueError as e:
        raise CasperError(f"Invalid ports.bind_address: {bind_address!r}") from e
    ssh_port = _check_port(pt.get("ssh", int, 2222), "ports.ssh")
    vnc_port = _check_port(pt.get("vnc", int, 5901), "ports.vnc")
    pt.finish()

    # ---- [vnc] ----
    vn = top.table("vnc")
    vnc_geometry = vn.get("geometry", str, "1920x1080")
    if not _GEOMETRY_RE.match(vnc_geometry):
        raise CasperError("vnc.geometry must be WIDTHxHEIGHT, e.g. 1920x1080")
    vn.finish()

    # ---- [[extra_ports]] ----
    extra_ports: list[PortMap] = []
    for t in top.array_of_tables("extra_ports"):
        host = _check_port(t.get("host", int), f"{t.where}.host")
        cport = _check_port(t.get("container", int), f"{t.where}.container")
        proto = t.get("protocol", str, "tcp")
        t.finish()
        if proto not in ("tcp", "udp"):
            raise CasperError(f"{t.where}.protocol must be 'tcp' or 'udp'")
        if cport in (SSH_CONTAINER_PORT, VNC_CONTAINER_PORT):
            raise CasperError(f"{t.where}: container ports 22/5901 are reserved")
        extra_ports.append(PortMap(host, cport, proto))

    seen: set[tuple[int, str]] = set()
    for pm in [PortMap(ssh_port, 22, "tcp"), PortMap(vnc_port, 5901, "tcp"), *extra_ports]:
        if (pm.host, pm.protocol) in seen:
            raise CasperError(f"Duplicate host port {pm.host}/{pm.protocol}")
        seen.add((pm.host, pm.protocol))

    # ---- [[mounts]] ----
    home = f"/home/{user}"
    reserved = {str(vivado_dir), home, f"{home}/.Xilinx", f"{home}/work", f"{home}/toolflow"}
    mounts: list[Mount] = []
    for t in top.array_of_tables("mounts"):
        hpath = _host_path(t.get("host", str), base)
        cpath = t.get("container", str)
        mode = t.get("mode", str, "rw")
        t.finish()
        if mode not in ("ro", "rw"):
            raise CasperError(f"{t.where}.mode must be 'ro' or 'rw'")
        if not hpath.exists():
            raise CasperError(f"{t.where}.host does not exist: {hpath}")
        if cpath == "~":
            cpath = home
        elif cpath.startswith("~/"):
            cpath = f"{home}/{cpath[2:]}"
        if not cpath.startswith("/"):
            raise CasperError(f"{t.where}.container must be absolute or start with ~/: {cpath!r}")
        cpath = os.path.normpath(cpath)
        if ":" in str(hpath) or ":" in cpath:
            raise CasperError(f"{t.where}: paths must not contain ':'")
        if cpath in reserved:
            raise CasperError(f"{t.where}.container conflicts with a reserved path: {cpath}")
        reserved.add(cpath)
        mounts.append(Mount(hpath, str(PurePosixPath(cpath)), mode))

    top.finish()

    return Config(
        path=cfg_path,
        toolflow=toolflow,
        name=name,
        user=user,
        password=password,
        vnc_password=vnc_password,
        mac=mac,
        shm_size=shm_size,
        tz=tz,
        vivado_dir=vivado_dir,
        vivado_mount=vivado_mount,
        license_dir=license_dir,
        work_dir=work_dir,
        bind_address=bind_address,
        ssh_port=ssh_port,
        vnc_port=vnc_port,
        vnc_geometry=vnc_geometry,
        mounts=mounts,
        extra_ports=extra_ports,
        warnings=warnings,
    )
