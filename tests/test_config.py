import tomllib
from pathlib import Path

import pytest

from casper_container.config import parse_config, render_template
from casper_container.errors import CasperError
from casper_container.podman import run_args
from casper_container.versions import list_versions, load_toolflow


@pytest.fixture
def env(tmp_path: Path):
    xilinx = tmp_path / "xilinx"
    (xilinx / "Vivado" / "2021.1").mkdir(parents=True)
    (xilinx / "Vivado" / "2021.1" / "settings64.sh").touch()
    lic = tmp_path / "lic"
    lic.mkdir()
    cfg_path = tmp_path / "proj" / "casper.toml"
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
