"""Toolflow versions shipped with the package (assets/versions/<ver>/)."""

from __future__ import annotations

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
    """Directory holding the Dockerfiles and build scripts (the build context)."""
    return Path(str(files("casperbox") / "assets"))


def versions_dir() -> Path:
    return assets_dir() / "versions"


def list_versions() -> list[str]:
    root = versions_dir()
    return sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "toolflow.toml").is_file()
    )


@dataclass(frozen=True)
class Toolflow:
    version: str
    params: dict[str, str]

    @property
    def version_dir(self) -> Path:
        return versions_dir() / self.version

    @property
    def dockerfile(self) -> Path:
        """versions/<version>/Dockerfile if the version has its own, else the shared one."""
        own = self.version_dir / "Dockerfile"
        return own if own.is_file() else assets_dir() / "toolflow" / "Dockerfile"

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
    def image(self) -> str:
        return f"casper-toolflow:{self.version}"


def load_toolflow(version: str) -> Toolflow:
    available = list_versions()
    if version not in available:
        raise CasperError(
            f"Unsupported toolflow version: {version!r} (available: {', '.join(available) or 'none'})"
        )
    path = versions_dir() / version / "toolflow.toml"
    with path.open("rb") as f:
        data = tomllib.load(f)

    params: dict[str, str] = {}
    for key in TOOLFLOW_KEYS:
        if key not in data:
            raise CasperError(f"{path} is missing {key}")
        params[key] = str(data[key])
    if params["TOOLFLOW_VERSION"] != version:
        raise CasperError(
            f"TOOLFLOW_VERSION={params['TOOLFLOW_VERSION']} in {path} does not match the directory name {version}"
        )
    tf = Toolflow(version=version, params=params)
    if not tf.pkgs_list.is_file():
        raise CasperError(f"{tf.pkgs_list} is missing; create it (an empty file or only comments is fine)")
    return tf
