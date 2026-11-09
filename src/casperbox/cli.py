"""casperbox command-line interface."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, podman
from .config import DEFAULT_CONFIG_NAME, Config, load_config, render_template
from .errors import CasperError
from .versions import list_versions, load_toolflow

app = typer.Typer(
    help="Build and run CASPER toolflow development containers with rootless podman.",
    no_args_is_help=True,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

ConfigOpt = Annotated[
    Path | None,
    typer.Option("-c", "--config", help=f"Config file (default: ./{DEFAULT_CONFIG_NAME})", show_default=False),
]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    typer.secho("[INFO]  ", fg=typer.colors.GREEN, nl=False, err=True)
    typer.echo(msg, err=True)


def warn(msg: str) -> None:
    typer.secho("[WARN]  ", fg=typer.colors.YELLOW, nl=False, err=True)
    typer.echo(msg, err=True)


def _print_table(rows: list[tuple[str, ...]]) -> None:
    """Print rows as left-aligned columns; the first row is the header."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        typer.echo("  ".join(f"{v:<{w}}" for v, w in zip(r, widths, strict=True)).rstrip())


def error(msg: str) -> None:
    typer.secho("[ERROR] ", fg=typer.colors.RED, nl=False, err=True)
    typer.echo(msg, err=True)


# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"casperbox {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    config: ConfigOpt = None,
    version: Annotated[
        bool, typer.Option("--version", "-v", callback=_version_callback, is_eager=True, help="Show the casperbox version")
    ] = False,
) -> None:
    ctx.obj = {"config": config}


def _load(ctx: typer.Context, config: Path | None) -> Config:
    """Load the config given by the command's -c, the global -c, or ./casperbox.toml."""
    path = config or (ctx.obj or {}).get("config")
    cfg = load_config(path)
    for w in cfg.warnings:
        warn(w)
    return cfg


def _prepare(ctx: typer.Context, config: Path | None) -> Config:
    podman.check_dependencies()
    return _load(ctx, config)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("list")
def list_cmd() -> None:
    """List supported toolflow versions."""
    rows = [("TOOLFLOW", "VIVADO", "SCILAB", "PYTHON")]
    for v in list_versions():
        tf = load_toolflow(v)
        rows.append((v, tf.vivado_version, tf.params["SCILAB_VERSION"], tf.params["PYTHON_VERSION"]))
    _print_table(rows)


@app.command()
def init(
    file: Annotated[Path, typer.Argument(help="Config file to create")] = Path(DEFAULT_CONFIG_NAME),
    toolflow: Annotated[
        str | None, typer.Option("--toolflow", "-t", help="Toolflow version (see `casperbox list`)")
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing file")] = False,
) -> None:
    """Create a config file from the template."""
    versions = list_versions()
    if toolflow is None:
        if len(versions) != 1:
            raise CasperError(f"Multiple toolflow versions available, choose one with --toolflow ({', '.join(versions)})")
        toolflow = versions[0]
    tf = load_toolflow(toolflow)

    path = file.expanduser().absolute()
    if path.exists() and not force:
        raise CasperError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file holds passwords: readable by the current user only
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(render_template(tf))
    os.chmod(path, 0o600)
    info(f"Created {path} (toolflow {tf.version})")

    if _in_git_repo(path.parent):
        warn(f"{path.parent} is inside a git repository; add {path.name} to .gitignore (it contains passwords)")

    cfg_arg = "" if path == Path.cwd() / DEFAULT_CONFIG_NAME else f" -c {shlex.quote(str(file))}"
    typer.echo(f"\nNext: edit {file}, then run: casperbox{cfg_arg} up")


def _in_git_repo(directory: Path) -> bool:
    if not shutil.which("git"):
        return False
    r = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() == "true"


@app.command()
def build(
    ctx: typer.Context,
    config: ConfigOpt = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Do not use the build cache")] = False,
) -> None:
    """Build the image for the config's toolflow version."""
    cfg = _prepare(ctx, config)
    _build(cfg, no_cache)


def _build(cfg: Config, no_cache: bool = False) -> None:
    info(f"Building image {cfg.toolflow.image} (the first build downloads a lot, please be patient)")
    podman.run(podman.build_args(cfg, no_cache))
    info(f"Image built: {cfg.toolflow.image}")


@app.command()
def up(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Start the container (builds the image if missing, recreates an existing container)."""
    cfg = _prepare(ctx, config)

    own = podman.get_container(cfg.name)
    if own:
        podman.check_owner(cfg, own)
    podman.check_ports_free(cfg, own)

    state = podman.image_state(cfg)
    if state == "missing":
        info(f"Image {cfg.toolflow.image} not found, building it")
        _build(cfg)
    elif state == "outdated":
        info(f"Image {cfg.toolflow.image} is out of date (build files, toolflow version or user changed), rebuilding it")
        _build(cfg)
    podman.check_image_matches(cfg)

    if not cfg.work_dir.exists():
        info(f"Creating work directory {cfg.work_dir}")
        cfg.work_dir.mkdir(parents=True)

    if own:
        info(f"Recreating container {cfg.name}")
        podman.remove_container(cfg.name)
    podman.run(podman.run_args(cfg), env=podman.container_env(cfg))
    _print_access(cfg)


def _print_access(cfg: Config) -> None:
    host = "localhost" if cfg.bind_address in ("0.0.0.0", "::") else cfg.bind_address
    cfg_arg = "" if cfg.path == Path.cwd() / DEFAULT_CONFIG_NAME else f" -c {shlex.quote(str(cfg.path))}"
    typer.echo(f"\nContainer {cfg.name} is up (toolflow {cfg.version})")
    typer.echo(f"  SSH  : ssh -p {cfg.ssh_port} {cfg.user}@{host}")
    typer.echo(f"  VNC  : {host}:{cfg.vnc_port}")
    typer.echo(f"  Shell: casperbox{cfg_arg} shell")
    for p in cfg.extra_ports:
        typer.echo(f"  Port : {p.host} -> {p.container}/{p.protocol}")


@app.command()
def down(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Stop and remove the container."""
    cfg = _prepare(ctx, config)
    own = podman.get_container(cfg.name)
    if not own:
        info(f"Container {cfg.name} does not exist")
        return
    podman.check_owner(cfg, own)
    podman.remove_container(cfg.name)
    info(f"Container {cfg.name} removed")


@app.command()
def restart(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Recreate the container (re-reads the config)."""
    down(ctx, config)
    up(ctx, config)


@app.command()
def shell(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Open a login shell in the container as the container user."""
    cfg = _prepare(ctx, config)
    own = podman.get_container(cfg.name)
    if not own:
        raise CasperError(f"Container {cfg.name} does not exist, please run: casperbox up")
    if own.state != "running":
        raise CasperError(f"Container {cfg.name} is {own.state}, please run: casperbox up")
    podman.exec_(["podman", "exec", "-it", "-u", cfg.user, "-w", cfg.home, cfg.name, "bash", "-l"])


@app.command()
def status(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Show the image and container status."""
    cfg = _prepare(ctx, config)
    image_note = {
        "missing": "not built",
        "outdated": "out of date, `casperbox up` or `casperbox build` will rebuild it",
        "current": "up to date",
    }[podman.image_state(cfg)]
    typer.echo(f"{cfg.toolflow.image}: {image_note}")
    own = podman.get_container(cfg.name)
    if not own:
        typer.echo(f"{cfg.name}: not created")
        return
    typer.echo(f"{own.name}: {own.status}")
    for p in own.ports:
        typer.echo(f"  {p.host} -> {p.container}/{p.protocol}")


@app.command()
def logs(
    ctx: typer.Context,
    config: ConfigOpt = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output")] = False,
) -> None:
    """Show the container logs."""
    cfg = _prepare(ctx, config)
    cmd = ["podman", "logs"]
    if follow:
        cmd.append("-f")
    podman.exec_([*cmd, cfg.name])


@app.command("config")
def config_cmd(ctx: typer.Context, config: ConfigOpt = None) -> None:
    """Show the resolved config and the podman commands (passwords masked)."""
    cfg = _load(ctx, config)
    rows = [
        ("config", str(cfg.path)),
        ("toolflow", cfg.version),
        ("image", cfg.toolflow.image),
        ("container", cfg.name),
        ("user", f"{cfg.user} ({os.getuid()}:{os.getgid()})"),
        ("vivado", f"{cfg.vivado_dir} ({cfg.vivado_mount})"),
        ("license", str(cfg.license_dir)),
        ("work", str(cfg.work_dir)),
        ("network", podman.pasta_network(cfg)),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        typer.echo(f"{k:<{width}}  {v}")

    env = podman.container_env(cfg)
    typer.echo("\nenvironment:")
    for k, v in env.items():
        typer.echo(f"  {k}={'******' if k in podman.SECRET_ENV else v}")
    typer.echo("\nbuild:")
    typer.echo("  " + shlex.join(podman.build_args(cfg)))
    typer.echo("\nrun:")
    typer.echo("  " + shlex.join(podman.run_args(cfg)))


@app.command()
def ps() -> None:
    """List all containers created by casperbox."""
    podman.check_dependencies()
    containers = podman.list_containers()
    if not containers:
        typer.echo("No casperbox containers")
        return
    rows = [("NAME", "TOOLFLOW", "STATUS", "PORTS", "CONFIG")]
    for c in containers:
        ports = ",".join(f"{p.host}->{p.container}/{p.protocol}" for p in c.ports)
        rows.append((c.name, c.labels.get(podman.LABEL_TOOLFLOW, ""), c.status, ports, c.config_path))
    _print_table(rows)


def main() -> None:
    try:
        app()
    except CasperError as e:
        error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
