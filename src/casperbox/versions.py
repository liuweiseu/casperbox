"""Toolflow versions: built-in (assets/versions/<ver>/) and plugins in the user's directories."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .errors import CasperError

# Keys every toolflow.toml must define; all of them are passed as build args.
TOOLFLOW_KEYS = (
    "TOOLFLOW_VERSION",
    "BASE_IMAGE",
    "VIVADO_VERSION",
    "MINICONDA_INSTALLER",
    "PYTHON_VERSION",
    "CONDA_ENV_NAME",
    "MLIB_DEVEL_REPO",
    "MLIB_DEVEL_BRANCH",
    "DEVICE_TREE_XLNX_REPO",
    "DEVICE_TREE_XLNX_BRANCH",
    "GIT_CLONE_DEPTH",
    "SCILAB_VERSION",
    "SCILAB_URL",
)


def assets_dir() -> Path:
    """Directory holding the Dockerfiles and build scripts (sources of the build context)."""
    return Path(str(files("casperbox") / "assets"))


def versions_dir() -> Path:
    """Built-in toolflow versions."""
    return assets_dir() / "versions"


# ---------------------------------------------------------------------------
# Plugins: toolflow versions defined by users, in the same layout as the built-in ones
# ---------------------------------------------------------------------------

PLUGIN_PATH_ENV = "CASPERBOX_PLUGIN_PATH"
# Used in image tags (casper-toolflow:<version>) and directory names
VERSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def user_plugin_dir() -> Path:
    """~/.config/casperbox/versions (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "casperbox" / "versions"


def plugin_dirs() -> list[Path]:
    """Directories searched for plugins, in order: CASPERBOX_PLUGIN_PATH entries, then the user's."""
    dirs: list[Path] = []
    for entry in os.environ.get(PLUGIN_PATH_ENV, "").split(os.pathsep):
        if entry.strip():
            dirs.append(Path(entry.strip()).expanduser().absolute())
    dirs.append(user_plugin_dir().absolute())
    unique: list[Path] = []
    for d in dirs:
        if d not in unique:
            unique.append(d)
    return unique


def _version_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "toolflow.toml").is_file())


def discover() -> dict[str, Path]:
    """All toolflow versions: name -> version directory (built-in first, then plugins).

    A plugin may not use the name of a built-in version or of a plugin in another directory.
    """
    found = {d.name: d for d in _version_dirs(versions_dir())}
    for root in plugin_dirs():
        for d in _version_dirs(root):
            if not VERSION_NAME_RE.match(d.name):
                raise CasperError(
                    f"Invalid plugin name {d.name!r} ({d}): use lowercase letters, digits, '.', '_' and '-'"
                )
            if d.name in found:
                other = found[d.name]
                what = "a built-in version" if other.parent == versions_dir() else f"the plugin in {other}"
                raise CasperError(f"Plugin {d} has the same name as {what}; rename or remove one of them")
            found[d.name] = d
    return found


def list_versions() -> list[str]:
    return sorted(discover())


def builtin_versions() -> list[str]:
    return [d.name for d in _version_dirs(versions_dir())]


@dataclass(frozen=True)
class Toolflow:
    version: str
    params: dict[str, str]
    version_dir: Path

    @property
    def builtin(self) -> bool:
        return self.version_dir.parent == versions_dir()

    @property
    def source(self) -> str:
        """'built-in' or the plugin directory (with ~ for the home directory)."""
        if self.builtin:
            return "built-in"
        home = str(Path.home())
        s = str(self.version_dir)
        return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s

    @property
    def own_dockerfile(self) -> bool:
        return (self.version_dir / "Dockerfile").is_file()

    @property
    def dockerfile(self) -> Path:
        """versions/<version>/Dockerfile if the version has its own, else the shared one."""
        return self.version_dir / "Dockerfile" if self.own_dockerfile else assets_dir() / "toolflow" / "Dockerfile"

    @property
    def pkgs_list(self) -> Path:
        """Extra apt packages of this version (versions/<version>/pkgs.list; required, may be empty)."""
        return self.version_dir / "pkgs.list"

    @property
    def patch_scripts(self) -> list[Path]:
        """Version-specific fixes (versions/<version>/patch/*.sh), in the order the build runs them."""
        d = self.version_dir / "patch"
        return sorted((p for p in d.glob("*.sh") if p.is_file()), key=lambda p: p.name) if d.is_dir() else []

    @property
    def vivado_version(self) -> str:
        return self.params["VIVADO_VERSION"]

    @property
    def os(self) -> str:
        """Base OS of the image: docker.io/library/ubuntu:20.04 -> ubuntu:20.04."""
        return self.params["BASE_IMAGE"].rsplit("/", 1)[-1]

    @property
    def image(self) -> str:
        return f"casper-toolflow:{self.version}"


def load_toolflow(version: str) -> Toolflow:
    found = discover()
    if version not in found:
        searched = ", ".join(str(d) for d in plugin_dirs())
        raise CasperError(
            f"Unsupported toolflow version: {version!r} (available: {', '.join(sorted(found)) or 'none'}; "
            f"plugins are searched in {searched})"
        )
    path = found[version] / "toolflow.toml"
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise CasperError(f"{path}: {e}") from e

    params: dict[str, str] = {}
    for key in TOOLFLOW_KEYS:
        if key not in data:
            raise CasperError(f"{path} is missing {key}")
        params[key] = str(data[key])
    if params["TOOLFLOW_VERSION"] != version:
        raise CasperError(
            f"TOOLFLOW_VERSION={params['TOOLFLOW_VERSION']} in {path} does not match the directory name {version}"
        )
    tf = Toolflow(version=version, params=params, version_dir=found[version])
    if not tf.pkgs_list.is_file():
        raise CasperError(f"{tf.pkgs_list} is missing; create it (an empty file or only comments is fine)")
    return tf
