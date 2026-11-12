"""Toolflow version plugins: discovery, `plugin new/check/path`, build context and build hash."""

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from casperbox import cli, plugins, podman, versions
from casperbox.config import parse_config
from casperbox.errors import CasperError

BUILTIN = "m2021a"


@pytest.fixture
def user_dir() -> Path:
    """~/.config/casperbox/versions of the test (XDG_CONFIG_HOME is a temp dir, see conftest.py)."""
    return versions.user_plugin_dir()


def make_plugin(root: Path, name: str, **changes: str) -> Path:
    """A plugin copied from the built-in version, with toolflow.toml keys changed."""
    new = plugins.create_plugin(name, BUILTIN, root)
    if changes:
        toml = new.path / "toolflow.toml"
        text = toml.read_text()
        for key, value in changes.items():
            text = "\n".join(f'{key} = "{value}"' if line.startswith(f"{key} =") else line
                             for line in text.splitlines()) + "\n"
        toml.write_text(text)
    return new.path


def config_for(tmp_path: Path, version: str):
    xilinx = tmp_path / "xilinx"
    (xilinx / "Vivado" / "2021.1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lic").mkdir(exist_ok=True)
    doc = {"version": version, "vivado": {"dir": str(xilinx)}, "paths": {"license_dir": str(tmp_path / "lic")}}
    return parse_config(doc, tmp_path / "casperbox.toml")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_no_plugins_only_builtin():
    assert versions.list_versions() == versions.builtin_versions() == [BUILTIN]
    assert versions.load_toolflow(BUILTIN).source == "built-in"


def test_user_plugin_is_listed_and_loaded(user_dir):
    path = make_plugin(user_dir, "m2025a", VIVADO_VERSION="2025.1")
    assert versions.list_versions() == [BUILTIN, "m2025a"]
    tf = versions.load_toolflow("m2025a")
    assert tf.version_dir == path and not tf.builtin
    assert tf.vivado_version == "2025.1"
    assert tf.image == "casper-toolflow:m2025a"
    assert tf.dockerfile == versions.assets_dir() / "toolflow" / "Dockerfile"  # shared one


def test_plugin_path_env(tmp_path, monkeypatch, user_dir):
    a, b = tmp_path / "a", tmp_path / "b"
    make_plugin(a, "lab-a")
    make_plugin(b, "lab-b")
    make_plugin(user_dir, "mine")
    monkeypatch.setenv(versions.PLUGIN_PATH_ENV, f"{a}{os.pathsep}{b}{os.pathsep}")
    assert versions.plugin_dirs() == [a, b, user_dir]
    assert versions.list_versions() == ["lab-a", "lab-b", BUILTIN, "mine"]
    assert versions.load_toolflow("lab-b").version_dir == b / "lab-b"


def test_plugin_may_not_shadow_builtin(user_dir):
    (user_dir / BUILTIN).mkdir(parents=True)
    (user_dir / BUILTIN / "toolflow.toml").write_text("")
    with pytest.raises(CasperError, match="same name as a built-in version"):
        versions.list_versions()


def test_same_plugin_in_two_directories(tmp_path, monkeypatch, user_dir):
    make_plugin(tmp_path / "shared", "lab")  # not searched yet
    make_plugin(user_dir, "lab")
    monkeypatch.setenv(versions.PLUGIN_PATH_ENV, str(tmp_path / "shared"))
    with pytest.raises(CasperError, match=f"same name as the plugin in {tmp_path / 'shared' / 'lab'}"):
        versions.load_toolflow("lab")


def test_invalid_plugin_directory_name(user_dir):
    bad = user_dir / "M2025A"
    bad.mkdir(parents=True)
    (bad / "toolflow.toml").write_text("")
    with pytest.raises(CasperError, match="Invalid plugin name 'M2025A'"):
        versions.list_versions()


def test_unknown_version_mentions_plugin_dirs(user_dir):
    with pytest.raises(CasperError, match=f"plugins are searched in {user_dir}"):
        versions.load_toolflow("m2099a")


def test_broken_plugin_is_reported(user_dir):
    path = make_plugin(user_dir, "broken")
    (path / "toolflow.toml").write_text('TOOLFLOW_VERSION = "broken"\n')
    with pytest.raises(CasperError, match="is missing BASE_IMAGE"):
        versions.load_toolflow("broken")
    (path / "toolflow.toml").write_text("TOOLFLOW_VERSION = \n")
    with pytest.raises(CasperError, match="toolflow.toml"):
        versions.load_toolflow("broken")


def test_config_with_plugin_version(tmp_path, user_dir):
    make_plugin(user_dir, "m2025a")
    cfg = config_for(tmp_path, "m2025a")
    assert cfg.version == "m2025a" and cfg.image == "casper-toolflow:m2025a"
    assert "casperbox.toolflow=m2025a" in podman.run_args(cfg)


# ---------------------------------------------------------------------------
# plugin new
# ---------------------------------------------------------------------------

def test_create_plugin_from_builtin(user_dir):
    new = plugins.create_plugin("m2025a")
    assert new.path == user_dir / "m2025a" and new.template == BUILTIN and new.searched
    src = versions.versions_dir() / BUILTIN
    toml = (new.path / "toolflow.toml").read_text()
    assert 'TOOLFLOW_VERSION = "m2025a"' in toml and "m2021a version definition" not in toml
    assert "Plugin created from m2021a" in toml
    assert (new.path / "pkgs.list").read_text() == (src / "pkgs.list").read_text()
    assert sorted(p.name for p in (new.path / "patch").iterdir()) == ["50-libudev.sh"]
    readme = (new.path / "README.md").read_text()
    assert "plugin: m2025a" in readme and "Created from `m2021a`" in readme and "$" not in readme
    assert not (new.path / "Dockerfile").exists()  # m2021a uses the shared Dockerfile
    assert versions.load_toolflow("m2025a").params["VIVADO_VERSION"] == "2021.1"


def test_create_plugin_from_plugin_with_own_dockerfile(user_dir):
    first = make_plugin(user_dir, "base")
    (first / "Dockerfile").write_text("FROM scratch\n")
    for p in (first / "patch").iterdir():
        p.unlink()
    (first / "patch").rmdir()
    new = plugins.create_plugin("derived", "base")
    assert (new.path / "Dockerfile").read_text() == "FROM scratch\n"
    assert (new.path / "patch").is_dir() and not any((new.path / "patch").iterdir())
    assert versions.load_toolflow("derived").dockerfile == new.path / "Dockerfile"


def test_create_plugin_outside_the_search_path(tmp_path):
    new = plugins.create_plugin("lab", directory=tmp_path / "shared")
    assert new.path == tmp_path / "shared" / "lab" and not new.searched
    assert "lab" not in versions.list_versions()


def test_create_plugin_refuses(user_dir, tmp_path, monkeypatch):
    with pytest.raises(CasperError, match="built-in toolflow version"):
        plugins.create_plugin(BUILTIN)
    with pytest.raises(CasperError, match="Invalid plugin name"):
        plugins.create_plugin("My Version")
    with pytest.raises(CasperError, match="Unsupported toolflow version"):
        plugins.create_plugin("x", "m1999a")

    plugins.create_plugin("lab")
    with pytest.raises(CasperError, match="already exists"):
        plugins.create_plugin("lab")
    with pytest.raises(CasperError, match="cannot be created from itself"):
        plugins.create_plugin("lab", "lab", force=True)

    # the same name in another plugin directory
    monkeypatch.setenv(versions.PLUGIN_PATH_ENV, str(tmp_path / "shared"))
    with pytest.raises(CasperError, match=f"already exists in {user_dir / 'lab'}"):
        plugins.create_plugin("lab", directory=tmp_path / "shared")


def test_create_plugin_force_replaces(user_dir):
    path = plugins.create_plugin("lab").path
    (path / "extra.txt").write_text("old")
    plugins.create_plugin("lab", force=True)
    assert not (path / "extra.txt").exists() and (path / "toolflow.toml").is_file()


# ---------------------------------------------------------------------------
# plugin check
# ---------------------------------------------------------------------------

def test_check_ok(user_dir):
    make_plugin(user_dir, "lab", VIVADO_VERSION="2025.1")
    rows = dict(plugins.check_version("lab"))
    assert rows["vivado"] == "2025.1" and rows["patches"] == "50-libudev.sh"
    assert rows["source"] == versions.load_toolflow("lab").source != "built-in"
    assert rows["pkgs.list"].endswith("package(s)")
    assert dict(plugins.check_version(BUILTIN))["source"] == "built-in"


def test_check_finds_problems(user_dir):
    path = make_plugin(user_dir, "lab")
    (path / "patch" / "10-bad.sh").write_text("if true; then\n")
    (path / "pkgs.list").write_text("git\nBad_Package\nvim?\n")
    with pytest.raises(CasperError) as e:
        plugins.check_version("lab")
    assert "patch/10-bad.sh: syntax error" in str(e.value)
    assert "invalid package name(s): Bad_Package" in str(e.value)

    (path / "pkgs.list").unlink()
    with pytest.raises(CasperError, match="pkgs.list is missing"):
        plugins.check_version("lab")


def test_package_entries(tmp_path):
    f = tmp_path / "pkgs.list"
    f.write_text("# comment\n\ngit  # version control\n  vim? \n")
    assert plugins.package_entries(f) == ["git", "vim?"]


# ---------------------------------------------------------------------------
# Build context and hash
# ---------------------------------------------------------------------------

def test_build_context_contains_the_plugin(tmp_path, user_dir):
    make_plugin(user_dir, "lab")
    cfg = config_for(tmp_path, "lab")
    with podman.build_context(cfg) as ctx:
        assert (ctx / "common" / "scripts" / "run_patches.sh").is_file()
        assert (ctx / "toolflow" / "Dockerfile").is_file()
        assert (ctx / "versions" / "lab" / "pkgs.list").is_file()
        assert (ctx / "versions" / "lab" / "patch" / "50-libudev.sh").is_file()
        assert not (ctx / "versions" / BUILTIN).exists()
        args = podman.build_args(cfg, ctx)
        assert args[args.index("-f") + 1] == str(ctx / "toolflow" / "Dockerfile") and args[-1] == str(ctx)
        assert f"casperbox.toolflow-source={cfg.toolflow.source}" in args
    assert not ctx.exists()  # removed after the build


def test_build_context_uses_the_plugin_dockerfile(tmp_path, user_dir):
    path = make_plugin(user_dir, "lab")
    (path / "Dockerfile").write_text("FROM scratch\n")
    cfg = config_for(tmp_path, "lab")
    with podman.build_context(cfg) as ctx:
        args = podman.build_args(cfg, ctx)
        assert args[args.index("-f") + 1] == str(ctx / "versions" / "lab" / "Dockerfile")
        assert (ctx / "versions" / "lab" / "Dockerfile").read_text() == "FROM scratch\n"


def test_build_hash_follows_plugin_files_not_their_location(tmp_path, monkeypatch, user_dir):
    path = make_plugin(user_dir, "lab")
    h1 = podman.build_hash(config_for(tmp_path, "lab"))

    with (path / "pkgs.list").open("a") as f:
        f.write("htop\n")
    h2 = podman.build_hash(config_for(tmp_path, "lab"))
    assert h2 != h1  # plugin changed: `up` rebuilds

    (path / "patch" / "README.txt").write_text("notes")
    assert podman.build_hash(config_for(tmp_path, "lab")) != h2  # every file of the plugin counts
    (path / "patch" / "README.txt").unlink()

    # moving the plugin to another searched directory keeps the image up to date
    shared = tmp_path / "shared"
    shared.mkdir()
    path.rename(shared / "lab")
    monkeypatch.setenv(versions.PLUGIN_PATH_ENV, str(shared))
    assert podman.build_hash(config_for(tmp_path, "lab")) == h2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def invoke(*args: str):
    return CliRunner().invoke(cli.app, list(args))


def test_cli_plugin_new_list_check(user_dir):
    r = invoke("plugin", "new", "m2025a")
    assert r.exit_code == 0, r.output
    assert f"Created plugin m2025a in {user_dir / 'm2025a'}" in r.output
    assert "casperbox init --toolflow m2025a" in r.output

    r = invoke("list")
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0].split() == ["TOOLFLOW", "OS", "VIVADO", "SCILAB", "PYTHON", "SOURCE"]
    builtin = versions.load_toolflow(BUILTIN)
    assert lines[1].split()[:2] == [BUILTIN, builtin.os] and lines[1].split()[-1] == "built-in"
    assert builtin.os == builtin.params["BASE_IMAGE"].rsplit("/", 1)[-1]
    assert lines[2].split()[0] == "m2025a"

    r = invoke("plugin", "check", "m2025a")
    assert r.exit_code == 0, r.output
    assert "m2025a: OK" in r.output and "casper-toolflow:m2025a" in r.output


def test_cli_plugin_new_outside_search_path_warns(tmp_path):
    r = invoke("plugin", "new", "lab", "--dir", str(tmp_path / "shared"))
    assert r.exit_code == 0, r.output
    assert "is not searched for plugins" in r.output and "export CASPERBOX_PLUGIN_PATH=" in r.output


def test_cli_plugin_new_errors(user_dir):
    r = invoke("plugin", "new", BUILTIN)
    assert r.exit_code != 0 and isinstance(r.exception, CasperError)


def test_cli_init_with_plugin(tmp_path, user_dir, monkeypatch):
    make_plugin(user_dir, "m2025a")
    monkeypatch.chdir(tmp_path)
    r = invoke("init")
    assert isinstance(r.exception, CasperError) and "m2021a, m2025a" in str(r.exception)
    r = invoke("init", "--toolflow", "m2025a")
    assert r.exit_code == 0, r.output
    assert 'version = "m2025a"' in (tmp_path / "casperbox.toml").read_text()


def test_cli_plugin_path(tmp_path, monkeypatch, user_dir):
    monkeypatch.setenv(versions.PLUGIN_PATH_ENV, str(tmp_path / "shared"))
    r = invoke("plugin", "path")
    assert r.exit_code == 0, r.output
    assert f"CASPERBOX_PLUGIN_PATH={tmp_path / 'shared'}" in r.output
    assert f"{tmp_path / 'shared'}  (CASPERBOX_PLUGIN_PATH, does not exist)" in r.output
    assert f"{user_dir}  (user, does not exist)" in r.output
