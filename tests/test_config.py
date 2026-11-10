import os
import tomllib
from pathlib import Path

import pytest

from casperbox.config import parse_config, render_template
from casperbox.errors import CasperError
from casperbox.podman import run_args
from casperbox.versions import list_versions, load_toolflow


@pytest.fixture
def env(tmp_path: Path):
    xilinx = tmp_path / "xilinx"
    (xilinx / "Vivado" / "2021.1").mkdir(parents=True)
    (xilinx / "Vivado" / "2021.1" / "settings64.sh").touch()
    lic = tmp_path / "lic"
    lic.mkdir()
    cfg_path = tmp_path / "proj" / "casperbox.toml"
    cfg_path.parent.mkdir()

    def make(**overrides):
        doc = {
            "version": "m2021a",
            "vivado": {"dir": str(xilinx)},
            "paths": {"license_dir": str(lic)},
        }
        for key, value in overrides.items():
            section, _, name = key.partition("__")
            if name:
                doc.setdefault(section, {})[name] = value
            else:
                doc[section] = value
        return parse_config(doc, cfg_path)

    make.tmp = tmp_path
    return make


def test_template_parses_for_every_version():
    for v in list_versions():
        doc = tomllib.loads(render_template(load_toolflow(v)))
        assert doc["version"] == v
        assert doc["container"]["name"] == f"casper-{v}"


def test_defaults(env):
    cfg = env()
    assert cfg.name == "casper-m2021a"
    assert cfg.user == "casper"
    assert cfg.password == cfg.vnc_password == "casper"
    assert (cfg.ssh_port, cfg.vnc_port) == (2222, 5901)
    assert cfg.work_dir == env.tmp / "proj" / "work"  # relative to the config file
    assert cfg.warnings == []


def test_unknown_key_rejected(env):
    with pytest.raises(CasperError, match="Unknown key.*prot"):
        env(ports__prot=1)


def test_port_type_and_range(env):
    with pytest.raises(CasperError, match="must be a int"):
        env(ports__ssh="2222")
    with pytest.raises(CasperError, match="out of range"):
        env(ports__ssh=70000)
    with pytest.raises(CasperError, match="must be a int"):
        env(ports__ssh=True)


def test_duplicate_ports(env):
    with pytest.raises(CasperError, match="Duplicate host port 2222/tcp"):
        env(extra_ports=[{"host": 2222, "container": 80}])
    # same number on a different protocol is fine
    cfg = env(extra_ports=[{"host": 2222, "container": 80, "protocol": "udp"}])
    assert cfg.extra_ports[0].protocol == "udp"


def test_reserved_container_ports(env):
    with pytest.raises(CasperError, match="reserved"):
        env(extra_ports=[{"host": 9000, "container": 22}])


def test_mac(env):
    assert env(container__mac="02-AA-bb-cc-dd-ee").mac == "02:aa:bb:cc:dd:ee"
    with pytest.raises(CasperError, match="multicast"):
        env(container__mac="01:00:00:00:00:01")
    with pytest.raises(CasperError, match="Invalid container.mac"):
        env(container__mac="nope")


def test_vnc_password(env):
    with pytest.raises(CasperError, match="at least 6"):
        env(container__vnc_password="abc")
    assert env(container__vnc_password="123456789").warnings


def test_vivado_dir_must_not_shadow_system_paths(env):
    with pytest.raises(CasperError, match="would shadow"):
        env(vivado={"dir": "/usr"})
    with pytest.raises(CasperError, match="would shadow"):
        env(vivado={"dir": "/"})


def test_vivado_missing_settings_warns(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert env(vivado={"dir": str(empty)}).warnings


def test_mounts(env):
    cfg = env(mounts=[{"host": str(env.tmp / "lic"), "container": "~/data", "mode": "ro"}])
    assert cfg.mounts[0].container == "/home/casper/data"
    with pytest.raises(CasperError, match="reserved"):
        env(mounts=[{"host": str(env.tmp / "lic"), "container": "~/work"}])
    with pytest.raises(CasperError, match="does not exist"):
        env(mounts=[{"host": "/nonexistent/x", "container": "/x"}])
    with pytest.raises(CasperError, match="absolute"):
        env(mounts=[{"host": str(env.tmp / "lic"), "container": "data"}])


def test_unsupported_version(env):
    with pytest.raises(CasperError, match="Unsupported toolflow version"):
        env(version="m1999a")


def test_run_args(env):
    cfg = env(
        container__mac="02:00:00:00:00:01",
        container__name="lab",
        container__password="s3cret-pw",
        ports__bind_address="::1",
    )
    args = run_args(cfg)
    assert args[args.index("--network") + 1] == "pasta:-I,eth0,--ns-mac-addr,02:00:00:00:00:01"
    assert args[args.index("--hostname") + 1] == "lab"
    assert "[::1]:2222:22/tcp" in args
    # passwords are passed through the environment, never on the command line
    assert not any("s3cret-pw" in a for a in args)


def test_port_check_reports_all_conflicts(env, monkeypatch):
    import socket

    from casperbox import podman
    from casperbox.config import PortMap
    from casperbox.podman import ContainerInfo, check_ports_free

    held = []
    for _ in range(2):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        held.append(s)
    ssh, extra = (s.getsockname()[1] for s in held)
    other = ContainerInfo("other", "running", "Up", {}, [PortMap(45999, 22, "tcp")])
    monkeypatch.setattr(podman, "list_containers", lambda name=None: [other])
    cfg = env(
        ports={"bind_address": "127.0.0.1", "ssh": ssh, "vnc": 45999},
        extra_ports=[{"host": extra, "container": 80}],
    )
    try:
        with pytest.raises(CasperError) as e:
            check_ports_free(cfg, None)
    finally:
        for s in held:
            s.close()
    msg = str(e.value)
    assert "3 host port(s)" in msg
    assert f"{ssh}/tcp (ports.ssh): already in use" in msg
    assert "45999/tcp (ports.vnc): used by container other" in msg
    assert f"{extra}/tcp (extra_ports[0]): already in use" in msg


def test_build_hash_tracks_build_inputs(env, monkeypatch, tmp_path):
    import shutil

    from casperbox import podman, versions

    # work on a copy of the assets so the test can modify build files
    assets = tmp_path / "assets"
    shutil.copytree(versions.assets_dir(), assets)
    monkeypatch.setattr(versions, "assets_dir", lambda: assets)
    monkeypatch.setattr(podman, "assets_dir", lambda: assets)

    cfg = env()
    h1 = podman.build_hash(cfg)
    assert podman.build_hash(cfg) == h1  # deterministic
    assert f"casperbox.build-hash={h1}" in podman.build_args(cfg)

    with (assets / "common" / "packages" / "software.list").open("a") as f:
        f.write("htop\n")
    h2 = podman.build_hash(cfg)
    assert h2 != h1  # build file changed

    assert podman.build_hash(env(container__user="alice")) != h2  # build arg changed


def test_mounted_paths_must_exist(env, tmp_path):
    with pytest.raises(CasperError, match="vivado.dir does not exist"):
        env(vivado={"dir": str(tmp_path / "missing")})
    with pytest.raises(CasperError, match="paths.license_dir does not exist"):
        env(paths={"license_dir": str(tmp_path / "missing")})
    (tmp_path / "afile").touch()
    with pytest.raises(CasperError, match="vivado.dir is not a directory"):
        env(vivado={"dir": str(tmp_path / "afile")})
    with pytest.raises(CasperError, match=r"mounts\[0\].host does not exist"):
        env(mounts=[{"host": str(tmp_path / "missing"), "container": "/x"}])


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_unreadable_mounts_warn(env, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    secret = tmp_path / "secret.txt"
    secret.touch()
    locked.chmod(0o300)  # writable but not readable
    secret.chmod(0o200)
    try:
        cfg = env(
            paths={"license_dir": str(locked)},
            mounts=[{"host": str(secret), "container": "/data/secret.txt"}],
        )
    finally:
        locked.chmod(0o700)
        secret.chmod(0o600)
    assert any("paths.license_dir: no read permission on directory" in w for w in cfg.warnings)
    assert any("mounts[0].host: no read permission on file" in w for w in cfg.warnings)


def test_terminal_env(monkeypatch):
    from casperbox.podman import terminal_env

    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert terminal_env() == ["-e", "TERM=xterm-kitty", "-e", "COLORTERM=truecolor"]
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("COLORTERM")
    assert terminal_env() == ["-e", "TERM=xterm-256color"]


def test_check_owner_messages(env):
    from casperbox.podman import ContainerInfo, check_owner

    cfg = env()
    same = ContainerInfo(cfg.name, "running", "Up", {"casperbox.config": str(cfg.path)}, [])
    check_owner(cfg, same)  # same config: allowed (the container is recreated)

    foreign = ContainerInfo(cfg.name, "running", "Up", {}, [])
    with pytest.raises(CasperError, match="was not created by casperbox") as e:
        check_owner(cfg, foreign)
    assert f"podman rm -f {cfg.name}" in str(e.value)

    other = ContainerInfo(
        cfg.name, "running", "Up", {"casperbox.config": "/other/casper-dev.toml", "casperbox.kind": "dev"}, []
    )
    with pytest.raises(CasperError, match="created by /other/casper-dev.toml") as e:
        check_owner(cfg, other)
    assert "`casperbox-dev ps`" in str(e.value)
