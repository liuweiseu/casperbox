"""Config files: casperbox.toml (toolflow containers) and casper-dev.toml (dev containers).

Both share [container], [ports], [vnc], [build], [[mounts]] and [[extra_ports]]; casperbox.toml adds
the toolflow version, [vivado] and [paths]; casper-dev.toml adds the base OS and packages.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path, PurePosixPath
from string import Template
from typing import Any

from .errors import CasperError
from .versions import Toolflow, assets_dir, load_toolflow

RELEASE = "release"  # casperbox: CASPER toolflow containers
DEV = "dev"  # casperbox-dev: plain base containers for testing

DEFAULT_CONFIG_NAME = "casperbox.toml"
DEV_CONFIG_NAME = "casper-dev.toml"
CONFIG_NAMES = {RELEASE: DEFAULT_CONFIG_NAME, DEV: DEV_CONFIG_NAME}
TOOL_NAMES = {RELEASE: "casperbox", DEV: "casperbox-dev"}

# Default host ports; dev uses other ports so both can run side by side
DEFAULT_PORTS = {RELEASE: (2222, 5901), DEV: (2322, 5902)}

# Base images casperbox-dev has been tested with; others work with a warning
DEV_TESTED_OS = ("ubuntu:20.04", "ubuntu:22.04", "ubuntu:24.04", "ubuntu:26.04")
DEV_DEFAULT_OS = "ubuntu:26.04"

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
    kind: str  # RELEASE | DEV
    path: Path
    name: str
    user: str
    password: str
    vnc_password: str
    mac: str
    shm_size: str
    tz: str
    bind_address: str
    ssh_port: int
    vnc_port: int
    vnc_geometry: str
    apt_mirror: str = ""  # [build] apt_mirror; empty = the base image's default sources
    # Resource limits; empty/0 = no limit (podman run gets no limit options)
    memory: str = ""  # e.g. "64g"
    cpus: float = 0  # CPU time quota, e.g. 16 or 8.5
    cpuset: str = ""  # CPU cores, e.g. "0-15" or "0-7,16-23"
    mounts: list[Mount] = field(default_factory=list)
    extra_ports: list[PortMap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # casperbox only
    toolflow: Toolflow | None = None
    vivado_dir: Path | None = None
    vivado_mount: str = "ro"
    license_dir: Path | None = None
    work_dir: Path | None = None
    # casperbox-dev only
    os: str = ""  # as written in the config, e.g. ubuntu:26.04
    base_image: str = ""  # fully qualified, e.g. docker.io/library/ubuntu:26.04
    packages: list[str] = field(default_factory=list)

    @property
    def tool(self) -> str:
        return TOOL_NAMES[self.kind]

    @property
    def version(self) -> str:
        return self.toolflow.version if self.toolflow else ""

    @property
    def describe(self) -> str:
        return f"toolflow {self.version}" if self.kind == RELEASE else self.os

    @property
    def image(self) -> str:
        if self.toolflow:
            return self.toolflow.image
        # one image per dev config: each may install different packages
        return f"casperbox-dev:{self.name}"

    @property
    def dockerfile(self) -> Path:
        if self.toolflow:
            return self.toolflow.dockerfile
        return assets_dir() / "dev" / "Dockerfile"

    @property
    def build_sources(self) -> list[tuple[str, Path]]:
        """What the build context is made of: (path in the context, source directory).

        The image is built from these files (they also decide whether the image is outdated).
        A toolflow version can be a plugin outside the package, so the context is put together
        in a temporary directory for each build.
        """
        root = assets_dir()
        if self.toolflow:
            return [
                ("common", root / "common"),
                ("toolflow", root / "toolflow"),
                (f"versions/{self.toolflow.version}", self.toolflow.version_dir),
            ]
        return [("common", root / "common"), ("dev", root / "dev")]

    @property
    def dockerfile_in_context(self) -> str:
        """Path of the Dockerfile inside the build context."""
        if self.toolflow:
            if self.toolflow.own_dockerfile:
                return f"versions/{self.toolflow.version}/Dockerfile"
            return "toolflow/Dockerfile"
        return "dev/Dockerfile"

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
# Base image names (casperbox-dev)
# ---------------------------------------------------------------------------

_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_RE = re.compile(
    rf"^(?P<repo>{_COMPONENT}(?::[0-9]+)?(?:/{_COMPONENT})*):(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}})$"
)


def normalize_image(image: str) -> str:
    """Check "name:tag" and return the fully qualified name (docker.io/library/... for short names)."""
    m = _IMAGE_RE.match(image.strip())
    if not m:
        raise CasperError(
            f"Invalid os: {image!r}; expected an image name with a tag, e.g. {DEV_DEFAULT_OS!r} "
            "(find names and tags on https://hub.docker.com)"
        )
    repo, tag = m["repo"], m["tag"]
    parts = repo.split("/")
    if len(parts) == 1:
        repo = f"docker.io/library/{repo}"
    elif not ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        repo = f"docker.io/{repo}"
    return f"{repo}:{tag}"


def os_slug(image: str) -> str:
    """ubuntu:26.04 -> ubuntu-26.04 (used in the default container name)."""
    name, _, tag = image.strip().rpartition(":")
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", f"{name.rsplit('/', 1)[-1]}-{tag}")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _template(name: str) -> Template:
    return Template((files("casperbox") / "templates" / name).read_text(encoding="utf-8"))


def render_template(toolflow: Toolflow) -> str:
    return _template("casperbox.toml").substitute(version=toolflow.version, vivado_version=toolflow.vivado_version)


def render_dev_template(image: str) -> str:
    normalize_image(image)  # validate before writing the file
    return _template("casper-dev.toml").substitute(os=image, name=f"casper-dev-{os_slug(image)}")


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
_MEMORY_RE = re.compile(r"^[0-9]+[kmgKMG]$")
_CPUSET_RE = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")
# apt package name, optionally with :arch and/or =version
# apt mirror URL: http(s)://host[:port][/path], no characters that need quoting
_APT_MIRROR_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$")
_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*(:[a-z0-9-]+)?(=[A-Za-z0-9.+~:-]+)?$")


def _host_path(value: str, base: Path) -> Path:
    """Expand ~ and resolve relative paths against the config file's directory."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _require_dir(path: Path, what: str) -> None:
    if not path.exists():
        raise CasperError(f"{what} does not exist: {path}")
    if not path.is_dir():
        raise CasperError(f"{what} is not a directory: {path}")


def _check_readable(path: Path, what: str, warnings: list[str]) -> None:
    """Warn if the current user cannot read a mounted path.

    With userns keep-id the container user has the host user's UID/GID, so the host
    permissions apply inside the container. A directory also needs execute permission
    to be listed or entered.
    """
    mode = os.R_OK | os.X_OK if path.is_dir() else os.R_OK
    if not os.access(path, mode):
        kind = "directory" if path.is_dir() else "file"
        warnings.append(f"{what}: no read permission on {kind} {path}; it will not be readable in the container")


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

def resolve_config_path(path: Path | None, kind: str = RELEASE) -> Path:
    default = CONFIG_NAMES[kind]
    p = (path or Path(default)).expanduser()
    p = p if p.is_absolute() else Path.cwd() / p
    if not p.is_file():
        if path is None:
            raise CasperError(
                f"{default} not found in {Path.cwd()}; "
                f"run `{TOOL_NAMES[kind]} init` to create one, or pass -c FILE"
            )
        raise CasperError(f"Config file not found: {p}")
    return p.resolve()


def load_config(path: Path | None = None, kind: str = RELEASE) -> Config:
    cfg_path = resolve_config_path(path, kind)
    try:
        with cfg_path.open("rb") as f:
            doc = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise CasperError(f"{cfg_path}: {e}") from e
    return parse_dev_config(doc, cfg_path) if kind == DEV else parse_config(doc, cfg_path)


def _parse_container(top: _Table, default_name: str, warnings: list[str]) -> dict[str, Any]:
    c = top.table("container")
    name = c.get("name", str, default_name)
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
    limits = _parse_limits(c)
    c.finish()
    return {
        "name": name, "user": user, "password": password, "vnc_password": vnc_password,
        "mac": mac, "shm_size": shm_size, "tz": tz, **limits,
    }


def _parse_limits(c: _Table) -> dict[str, Any]:
    memory = c.get("memory", str, "").strip()
    if memory and (not _MEMORY_RE.match(memory) or int(memory[:-1]) == 0):
        raise CasperError(f"Invalid container.memory: {memory!r}; expected a size such as '64g' or '512m'")
    cpus = c.get("cpus", object, 0)
    if isinstance(cpus, bool) or not isinstance(cpus, int | float) or cpus < 0:
        raise CasperError(f"container.cpus must be a number >= 0 (0 = no limit), got {cpus!r}")
    cpuset = c.get("cpuset", str, "").replace(" ", "")
    if cpuset:
        if not _CPUSET_RE.match(cpuset):
            raise CasperError(f"Invalid container.cpuset: {cpuset!r}; expected cores such as '0-15' or '0-7,16-23'")
        for part in cpuset.split(","):
            lo, _, hi = part.partition("-")
            if hi and int(hi) < int(lo):
                raise CasperError(f"Invalid container.cpuset: {cpuset!r}; range {part} is reversed")
    return {"memory": memory.lower(), "cpus": float(cpus), "cpuset": cpuset}


def cpuset_cores(cpuset: str) -> list[int]:
    """CPU core numbers of a cpuset string such as '0-3,8'."""
    cores: list[int] = []
    for part in filter(None, cpuset.split(",")):
        lo, _, hi = part.partition("-")
        cores += range(int(lo), int(hi or lo) + 1)
    return cores


def _parse_network(top: _Table, default_ports: tuple[int, int]) -> dict[str, Any]:
    pt = top.table("ports")
    bind_address = pt.get("bind_address", str, "0.0.0.0")
    try:
        ipaddress.ip_address(bind_address)
    except ValueError as e:
        raise CasperError(f"Invalid ports.bind_address: {bind_address!r}") from e
    ssh_port = _check_port(pt.get("ssh", int, default_ports[0]), "ports.ssh")
    vnc_port = _check_port(pt.get("vnc", int, default_ports[1]), "ports.vnc")
    pt.finish()

    vn = top.table("vnc")
    vnc_geometry = vn.get("geometry", str, "1920x1080")
    if not _GEOMETRY_RE.match(vnc_geometry):
        raise CasperError("vnc.geometry must be WIDTHxHEIGHT, e.g. 1920x1080")
    vn.finish()

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

    return {
        "bind_address": bind_address, "ssh_port": ssh_port, "vnc_port": vnc_port,
        "vnc_geometry": vnc_geometry, "extra_ports": extra_ports,
    }


def _parse_build(top: _Table) -> dict[str, Any]:
    b = top.table("build")
    apt_mirror = b.get("apt_mirror", str, "").strip()
    b.finish()
    if apt_mirror and not _APT_MIRROR_RE.match(apt_mirror):
        raise CasperError(
            f"Invalid build.apt_mirror: {apt_mirror!r}; expected an http(s) URL of an Ubuntu mirror, "
            "e.g. 'http://us.archive.ubuntu.com/ubuntu'"
        )
    return {"apt_mirror": apt_mirror.rstrip("/")}


def _parse_mounts(top: _Table, base: Path, user: str, reserved: set[str], warnings: list[str]) -> list[Mount]:
    home = f"/home/{user}"
    reserved = {*reserved, home}
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
        _check_readable(hpath, f"{t.where}.host", warnings)
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
    return mounts


def parse_config(doc: dict[str, Any], cfg_path: Path) -> Config:
    """casperbox.toml"""
    base = cfg_path.parent
    top = _Table(doc, "")
    warnings: list[str] = []

    toolflow = load_toolflow(top.get("version", str))
    container = _parse_container(top, f"casper-{toolflow.version}", warnings)
    user = container["user"]

    # ---- [vivado] ----
    v = top.table("vivado")
    vivado_dir = _host_path(v.get("dir", str), base)
    vivado_mount = v.get("mount", str, "ro")
    if vivado_mount not in ("ro", "rw"):
        raise CasperError("vivado.mount must be 'ro' or 'rw'")
    v.finish()
    _require_dir(vivado_dir, "vivado.dir")
    _check_readable(vivado_dir, "vivado.dir", warnings)
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
    _require_dir(license_dir, "paths.license_dir")
    _check_readable(license_dir, "paths.license_dir", warnings)
    if work_dir.exists() and not work_dir.is_dir():
        raise CasperError(f"paths.work_dir is not a directory: {work_dir}")
    if work_dir.is_dir():  # created by `up` if missing
        _check_readable(work_dir, "paths.work_dir", warnings)

    network = _parse_network(top, DEFAULT_PORTS[RELEASE])
    build = _parse_build(top)
    home = f"/home/{user}"
    mounts = _parse_mounts(top, base, user, {str(vivado_dir), f"{home}/.Xilinx", f"{home}/work", f"{home}/toolflow"}, warnings)
    top.finish()

    return Config(
        kind=RELEASE,
        path=cfg_path,
        **container,
        **network,
        **build,
        mounts=mounts,
        warnings=warnings,
        toolflow=toolflow,
        vivado_dir=vivado_dir,
        vivado_mount=vivado_mount,
        license_dir=license_dir,
        work_dir=work_dir,
    )


def parse_dev_config(doc: dict[str, Any], cfg_path: Path) -> Config:
    """casper-dev.toml"""
    base = cfg_path.parent
    top = _Table(doc, "")
    warnings: list[str] = []

    os_image = top.get("os", str).strip()
    base_image = normalize_image(os_image)
    if base_image not in {normalize_image(t) for t in DEV_TESTED_OS}:
        warnings.append(f"os {os_image!r} has not been tested (tested: {', '.join(DEV_TESTED_OS)})")

    packages = top.get("packages", list, [])
    for pkg in packages:
        if not isinstance(pkg, str) or not _PACKAGE_RE.match(pkg):
            raise CasperError(f"Invalid entry in packages: {pkg!r} (expected an apt package name)")
    packages = list(dict.fromkeys(packages))  # drop duplicates, keep order

    container = _parse_container(top, f"casper-dev-{os_slug(os_image)}", warnings)
    network = _parse_network(top, DEFAULT_PORTS[DEV])
    build = _parse_build(top)
    mounts = _parse_mounts(top, base, container["user"], set(), warnings)
    top.finish()

    return Config(
        kind=DEV,
        path=cfg_path,
        **container,
        **network,
        **build,
        mounts=mounts,
        warnings=warnings,
        os=os_image,
        base_image=base_image,
        packages=packages,
    )
