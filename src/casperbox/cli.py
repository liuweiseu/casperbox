"""Command-line interfaces: `casperbox` (CASPER toolflow containers) and
`casperbox-dev` (plain base containers for testing). Both are built by make_app()
and share every command except `list` and `init`."""

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
from .config import (
    CONFIG_NAMES,
    DEV,
    DEV_DEFAULT_OS,
    DEV_TESTED_OS,
    RELEASE,
    TOOL_NAMES,
    Config,
    load_config,
    normalize_image,
    render_dev_template,
    render_template,
)
from .errors import CasperError
from .versions import list_versions, load_toolflow

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    typer.secho("[INFO]  ", fg=typer.colors.GREEN, nl=False, err=True)
    typer.echo(msg, err=True)


def warn(msg: str) -> None:
    typer.secho("[WARN]  ", fg=typer.colors.YELLOW, nl=False, err=True)
    typer.echo(msg, err=True)


def error(msg: str) -> None:
    typer.secho("[ERROR] ", fg=typer.colors.RED, nl=False, err=True)
    typer.echo(msg, err=True)


def _print_table(rows: list[tuple[str, ...]]) -> None:
    """Print rows as left-aligned columns; the first row is the header."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        typer.echo("  ".join(f"{v:<{w}}" for v, w in zip(r, widths, strict=True)).rstrip())


def _in_git_repo(directory: Path) -> bool:
    if not shutil.which("git"):
        return False
    r = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.strip() == "true"


def _write_config(file: Path, content: str, force: bool) -> Path:
    path = file.expanduser().absolute()
    if path.exists() and not force:
        raise CasperError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    # The file holds passwords: readable by the current user only
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, 0o600)
    return path


def _after_init(path: Path, file: Path, tool: str, default_name: str) -> None:
    if _in_git_repo(path.parent):
        warn(f"{path.parent} is inside a git repository; add {path.name} to .gitignore (it contains passwords)")
    cfg_arg = "" if path == Path.cwd() / default_name else f" -c {shlex.quote(str(file))}"
    typer.echo(f"\nNext: edit {file}, then run: {tool}{cfg_arg} up")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def make_app(kind: str) -> typer.Typer:
    tool = TOOL_NAMES[kind]
    default_name = CONFIG_NAMES[kind]
    app = typer.Typer(
        help=(
            "Build and run CASPER toolflow development containers with rootless podman."
            if kind == RELEASE
            else "Build and run plain base containers (SSH + VNC desktop) for testing, with rootless podman."
        ),
        no_args_is_help=True,
        add_completion=True,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    # Options whose help text depends on the tool are given as default values (not in
    # Annotated[...]): annotations are evaluated by Typer later, outside this function.
    config_opt = typer.Option(None, "-c", "--config", help=f"Config file (default: ./{default_name})", show_default=False)

    def version_callback(value: bool) -> None:
        if value:
            typer.echo(f"{tool} {__version__}")
            raise typer.Exit()

    @app.callback()
    def main_callback(
        ctx: typer.Context,
        config: Path | None = config_opt,
        version: bool = typer.Option(
            False, "--version", "-v", callback=version_callback, is_eager=True, help=f"Show the {tool} version"
        ),
    ) -> None:
        ctx.obj = {"config": config}

    def load(ctx: typer.Context, config: Path | None) -> Config:
        """Load the config given by the command's -c, the global -c, or the default file."""
        cfg = load_config(config or (ctx.obj or {}).get("config"), kind)
        for w in cfg.warnings:
            warn(w)
        return cfg

    def prepare(ctx: typer.Context, config: Path | None) -> Config:
        podman.check_dependencies()
        return load(ctx, config)

    def do_build(cfg: Config, no_cache: bool = False) -> None:
        if cfg.kind == DEV:
            info(f"Checking base image {cfg.base_image}")
            podman.check_base_image(cfg)
        info(f"Building image {cfg.image} (the first build downloads a lot, please be patient)")
        podman.run(podman.build_args(cfg, no_cache))
        info(f"Image built: {cfg.image}")

    # ---- kind-specific commands: list, init ----

    if kind == RELEASE:

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
            file: Annotated[Path, typer.Argument(help="Config file to create")] = Path(default_name),
            toolflow: str | None = typer.Option(None, "--toolflow", "-t", help=f"Toolflow version (see `{tool} list`)"),
            force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing file")] = False,
        ) -> None:
            """Create a config file from the template."""
            versions = list_versions()
            if toolflow is None:
                if len(versions) != 1:
                    raise CasperError(
                        f"Multiple toolflow versions available, choose one with --toolflow ({', '.join(versions)})"
                    )
                toolflow = versions[0]
            tf = load_toolflow(toolflow)
            path = _write_config(file, render_template(tf), force)
            info(f"Created {path} (toolflow {tf.version})")
            _after_init(path, file, tool, default_name)

    else:

        @app.command("list")
        def list_cmd() -> None:
            """List tested base OS images (any apt-based image can be used)."""
            rows = [("OS", "IMAGE", "")]
            for o in DEV_TESTED_OS:
                rows.append((o, normalize_image(o), "default" if o == DEV_DEFAULT_OS else ""))
            _print_table(rows)

        @app.command()
        def init(
            file: Annotated[Path, typer.Argument(help="Config file to create")] = Path(default_name),
            os_image: str = typer.Option(
                DEV_DEFAULT_OS, "--os", help=f"Base OS image, name:tag (tested: see `{tool} list`)"
            ),
            force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite an existing file")] = False,
        ) -> None:
            """Create a config file from the template."""
            path = _write_config(file, render_dev_template(os_image), force)
            info(f"Created {path} (os {os_image})")
            if os_image not in DEV_TESTED_OS:
                warn(f"os {os_image!r} has not been tested (tested: {', '.join(DEV_TESTED_OS)})")
            _after_init(path, file, tool, default_name)

    # ---- shared commands ----

    @app.command()
    def build(
        ctx: typer.Context,
        config: Path | None = config_opt,
        no_cache: Annotated[bool, typer.Option("--no-cache", help="Do not use the build cache")] = False,
    ) -> None:
        """Build the image."""
        do_build(prepare(ctx, config), no_cache)

    def confirm_warnings(cfg: Config, yes: bool) -> None:
        """Ask before creating a container when the config produced warnings."""
        if not cfg.warnings or yes:
            return
        if not sys.stdin.isatty():
            raise CasperError(
                f"{len(cfg.warnings)} warning(s) above; not continuing without confirmation "
                "(stdin is not a terminal). Fix them, or pass --yes to continue anyway."
            )
        if not typer.confirm(f"{len(cfg.warnings)} warning(s) above. Continue creating the container?", default=False):
            info("Aborted, nothing was changed")
            raise typer.Exit(1)

    def do_up(cfg: Config) -> None:
        own = podman.get_container(cfg.name)
        if own:
            podman.check_owner(cfg, own)
        podman.check_ports_free(cfg, own)

        state = podman.image_state(cfg)
        if state == "missing":
            info(f"Image {cfg.image} not found, building it")
            do_build(cfg)
        elif state == "outdated":
            info(f"Image {cfg.image} is out of date (build files or settings changed), rebuilding it")
            do_build(cfg)
        podman.check_image_matches(cfg)

        if cfg.work_dir and not cfg.work_dir.exists():
            info(f"Creating work directory {cfg.work_dir}")
            cfg.work_dir.mkdir(parents=True)

        if own:
            info(f"Recreating container {cfg.name}")
            podman.remove_container(cfg.name)
        podman.run(podman.run_args(cfg), env=podman.container_env(cfg))

        host = "localhost" if cfg.bind_address in ("0.0.0.0", "::") else cfg.bind_address
        cfg_arg = "" if cfg.path == Path.cwd() / default_name else f" -c {shlex.quote(str(cfg.path))}"
        typer.echo(f"\nContainer {cfg.name} is up ({cfg.describe})")
        typer.echo(f"  SSH  : ssh -p {cfg.ssh_port} {cfg.user}@{host}")
        typer.echo(f"  VNC  : {host}:{cfg.vnc_port}")
        typer.echo(f"  Shell: {tool}{cfg_arg} shell")
        for p in cfg.extra_ports:
            typer.echo(f"  Port : {p.host} -> {p.container}/{p.protocol}")

    @app.command()
    def up(
        ctx: typer.Context,
        config: Path | None = config_opt,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation on warnings")] = False,
    ) -> None:
        """Start the container (builds the image if missing or outdated, recreates an existing container)."""
        cfg = prepare(ctx, config)
        confirm_warnings(cfg, yes)
        do_up(cfg)

    @app.command()
    def down(ctx: typer.Context, config: Path | None = config_opt) -> None:
        """Stop and remove the container."""
        do_down(prepare(ctx, config))

    def do_down(cfg: Config) -> None:
        own = podman.get_container(cfg.name)
        if not own:
            info(f"Container {cfg.name} does not exist")
            return
        podman.check_owner(cfg, own)
        podman.remove_container(cfg.name)
        info(f"Container {cfg.name} removed")

    @app.command()
    def restart(
        ctx: typer.Context,
        config: Path | None = config_opt,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation on warnings")] = False,
    ) -> None:
        """Recreate the container (re-reads the config)."""
        cfg = prepare(ctx, config)
        confirm_warnings(cfg, yes)  # before removing the old container
        do_down(cfg)
        do_up(cfg)

    @app.command()
    def shell(ctx: typer.Context, config: Path | None = config_opt) -> None:
        """Open a login shell in the container as the container user."""
        cfg = prepare(ctx, config)
        own = podman.get_container(cfg.name)
        if not own:
            raise CasperError(f"Container {cfg.name} does not exist, please run: {tool} up")
        if own.state != "running":
            raise CasperError(f"Container {cfg.name} is {own.state}, please run: {tool} up")
        podman.exec_(["podman", "exec", "-it", *podman.terminal_env(), "-u", cfg.user, "-w", cfg.home,
                      cfg.name, "bash", "-l"])

    @app.command()
    def status(ctx: typer.Context, config: Path | None = config_opt) -> None:
        """Show the image and container status."""
        cfg = prepare(ctx, config)
        image_note = {
            "missing": "not built",
            "outdated": f"out of date, `{tool} up` or `{tool} build` will rebuild it",
            "current": "up to date",
        }[podman.image_state(cfg)]
        typer.echo(f"{cfg.image}: {image_note}")
        own = podman.get_container(cfg.name)
        if not own:
            typer.echo(f"{cfg.name}: not created")
            return
        typer.echo(f"{own.name}: {own.status}")
        if own.ports:
            typer.echo("  Ports:")
            for p in own.ports:
                typer.echo(f"    {p.host} -> {p.container}/{p.protocol}")
        mounts = podman.container_mounts(own.name)
        if mounts:
            typer.echo("  Mounts:")
            width = max(len(m.source) for m in mounts)
            for m in mounts:
                typer.echo(f"    {m.source:<{width}} -> {m.destination} ({m.mode})")

    @app.command()
    def logs(
        ctx: typer.Context,
        config: Path | None = config_opt,
        follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output")] = False,
    ) -> None:
        """Show the container logs."""
        cfg = prepare(ctx, config)
        cmd = ["podman", "logs"]
        if follow:
            cmd.append("-f")
        podman.exec_([*cmd, cfg.name])

    @app.command("config")
    def config_cmd(ctx: typer.Context, config: Path | None = config_opt) -> None:
        """Show the resolved config and the podman commands (passwords masked)."""
        cfg = load(ctx, config)
        rows = [("config", str(cfg.path))]
        if cfg.kind == RELEASE:
            rows.append(("toolflow", cfg.version))
        else:
            rows += [("os", cfg.base_image), ("packages", " ".join(cfg.packages) or "-")]
        rows += [
            ("image", cfg.image),
            ("container", cfg.name),
            ("user", f"{cfg.user} ({os.getuid()}:{os.getgid()})"),
        ]
        if cfg.kind == RELEASE:
            rows += [
                ("vivado", f"{cfg.vivado_dir} ({cfg.vivado_mount})"),
                ("license", str(cfg.license_dir)),
                ("work", str(cfg.work_dir)),
            ]
        rows.append(("network", podman.pasta_network(cfg)))
        width = max(len(k) for k, _ in rows)
        for k, v in rows:
            typer.echo(f"{k:<{width}}  {v}")

        typer.echo("\nenvironment:")
        for k, v in podman.container_env(cfg).items():
            typer.echo(f"  {k}={'******' if k in podman.SECRET_ENV else v}")
        typer.echo("\nbuild:")
        typer.echo("  " + shlex.join(podman.build_args(cfg)))
        typer.echo("\nrun:")
        typer.echo("  " + shlex.join(podman.run_args(cfg)))

    @app.command()
    def ps() -> None:
        """List all containers created by this tool."""
        podman.check_dependencies()
        containers = [c for c in podman.list_containers() if c.kind == kind]
        if not containers:
            typer.echo(f"No {tool} containers")
            return
        label, header = (podman.LABEL_TOOLFLOW, "TOOLFLOW") if kind == RELEASE else (podman.LABEL_OS, "OS")
        rows = [("NAME", header, "STATUS", "PORTS", "CONFIG")]
        for c in containers:
            ports = ",".join(f"{p.host}->{p.container}/{p.protocol}" for p in c.ports)
            rows.append((c.name, c.labels.get(label, ""), c.status, ports, c.config_path))
        _print_table(rows)

    return app


app = make_app(RELEASE)
dev_app = make_app(DEV)


def _run(typer_app: typer.Typer) -> None:
    try:
        typer_app()
    except CasperError as e:
        error(str(e))
        sys.exit(1)


def main() -> None:
    _run(app)


def main_dev() -> None:
    _run(dev_app)


if __name__ == "__main__":
    main()
