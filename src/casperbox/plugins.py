"""Toolflow version plugins: create one from a template and check it.

A plugin is a directory with the same layout as a built-in version (assets/versions/<name>/),
in ~/.config/casperbox/versions/ or in a directory listed in CASPERBOX_PLUGIN_PATH. Discovery and
loading are in versions.py, so plugins work everywhere a built-in version does.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from .errors import CasperError
from .versions import (
    VERSION_NAME_RE,
    Toolflow,
    builtin_versions,
    discover,
    load_toolflow,
    plugin_dirs,
    user_plugin_dir,
)

_VERSION_LINE_RE = re.compile(r"^(\s*TOOLFLOW_VERSION\s*=\s*).*$", re.MULTILINE)


@dataclass(frozen=True)
class NewPlugin:
    path: Path
    template: str  # version it was copied from
    searched: bool  # whether the plugin's directory is searched (else CASPERBOX_PLUGIN_PATH is needed)


def default_template() -> str:
    """The newest built-in version."""
    versions = builtin_versions()
    if not versions:
        raise CasperError("No built-in toolflow version to use as a template")
    return versions[-1]


def create_plugin(name: str, template: str | None = None, directory: Path | None = None,
                  force: bool = False) -> NewPlugin:
    """Create plugin `name` in `directory` (default ~/.config/casperbox/versions) as a copy of
    the version `template` (default: the newest built-in version)."""
    if not VERSION_NAME_RE.match(name):
        raise CasperError(
            f"Invalid plugin name {name!r}: use lowercase letters, digits, '.', '_' and '-' "
            "(it becomes part of the image name casper-toolflow:<name>)"
        )
    found = discover()
    root = (directory.expanduser() if directory else user_plugin_dir()).absolute()
    target = root / name
    if name in builtin_versions():
        raise CasperError(f"{name!r} is a built-in toolflow version; choose another name")
    if name in found and found[name] != target:
        raise CasperError(f"Plugin {name!r} already exists in {found[name]}")
    if target.exists() and not force:
        raise CasperError(f"{target} already exists (use --force to replace it)")

    src: Toolflow = load_toolflow(template or default_template())
    if src.version_dir == target:
        raise CasperError("A plugin cannot be created from itself")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    text = (src.version_dir / "toolflow.toml").read_text(encoding="utf-8")
    text = _VERSION_LINE_RE.sub(lambda m: f'{m.group(1)}"{name}"', text, count=1)
    text = text.replace(f"# CASPER toolflow {src.version} version definition", f"# CASPER toolflow {name} version definition")
    text = text.replace(
        "# Managed by the maintainers and shipped with the casper tool; users do not edit it.",
        f"# Plugin created from {src.version} by `casperbox plugin new`; edit it for this version.",
    )
    (target / "toolflow.toml").write_text(text, encoding="utf-8")
    shutil.copy2(src.pkgs_list, target / "pkgs.list")
    if (src.version_dir / "patch").is_dir():
        shutil.copytree(src.version_dir / "patch", target / "patch",
                        ignore=shutil.ignore_patterns("__pycache__"))
    else:
        (target / "patch").mkdir()
    if src.own_dockerfile:
        shutil.copy2(src.version_dir / "Dockerfile", target / "Dockerfile")
    readme = Template((files("casperbox") / "templates" / "plugin-README.md").read_text(encoding="utf-8"))
    (target / "README.md").write_text(readme.substitute(name=name, template=src.version), encoding="utf-8")

    return NewPlugin(path=target, template=src.version, searched=root in plugin_dirs())


def package_entries(pkgs_list: Path) -> list[str]:
    """Packages in a package list (comments and blank lines removed), as install_pkg_list.sh reads it."""
    entries = []
    for line in pkgs_list.read_text(encoding="utf-8").splitlines():
        entry = "".join(line.split("#", 1)[0].split())
        if entry:
            entries.append(entry)
    return entries


def check_version(name: str) -> list[tuple[str, str]]:
    """Validate a toolflow version (built-in or plugin) without building it.

    Returns rows describing it; raises CasperError listing every problem found.
    """
    tf = load_toolflow(name)  # toolflow.toml keys, name, pkgs.list
    problems = []
    entries = package_entries(tf.pkgs_list)
    bad = [e for e in entries if not re.match(r"^[a-z0-9][a-z0-9+.:=~-]*\??$", e)]
    if bad:
        problems.append(f"pkgs.list: invalid package name(s): {', '.join(bad)}")
    bash = shutil.which("bash")
    for script in tf.patch_scripts:
        if bash:
            r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, check=False)
            if r.returncode != 0:
                problems.append(f"patch/{script.name}: syntax error: {r.stderr.strip()}")
    if problems:
        raise CasperError(f"Toolflow version {name} ({tf.version_dir}) has problems:\n  " + "\n  ".join(problems))
    return [
        ("version", tf.version),
        ("source", tf.source),
        ("vivado", tf.vivado_version),
        ("base image", tf.params["BASE_IMAGE"]),
        ("dockerfile", "own (Dockerfile)" if tf.own_dockerfile else "shared (toolflow/Dockerfile)"),
        ("pkgs.list", f"{len(entries)} package(s)"),
        ("patches", " ".join(p.name for p in tf.patch_scripts) or "-"),
        ("image", tf.image),
    ]
