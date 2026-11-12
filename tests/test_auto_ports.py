"""ports.ssh / ports.vnc = 0: `up` picks free host ports and writes them back into the config."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from casperbox import cli, podman
from casperbox.config import (
    DEV,
    DEV_DEFAULT_OS,
    PortMap,
    load_config,
    parse_dev_config,
    save_ports,
)
from casperbox.errors import CasperError
from casperbox.podman import ContainerInfo


def make(tmp_path: Path, ports: dict, extra_ports: list | None = None):
    data = {"os": DEV_DEFAULT_OS, "ports": ports}
    if extra_ports:
        data["extra_ports"] = extra_ports
    return parse_dev_config(data, tmp_path / "casper-dev.toml")


def test_zero_is_accepted_for_ssh_and_vnc_only(tmp_path):
    cfg = make(tmp_path, {"ssh": 0, "vnc": 0})  # two zeros are not a duplicate
    assert (cfg.ssh_port, cfg.vnc_port) == (0, 0)
    with pytest.raises(CasperError, match=r"extra_ports\[0\].host is out of range"):
        make(tmp_path, {"ssh": 0}, [{"host": 0, "container": 80}])
    with pytest.raises(CasperError, match=r"ports.ssh is out of range \(0 or 1-65535\)"):
        make(tmp_path, {"ssh": -1})


@pytest.fixture
def host(monkeypatch):
    """Fake host: `busy` ports are taken by other processes, `containers` are other containers."""
    state = {"busy": set(), "containers": []}
    monkeypatch.setattr(podman, "_port_free", lambda addr, port, proto: port not in state["busy"])
    monkeypatch.setattr(podman, "list_containers", lambda name=None: state["containers"])
    return state


def test_pick_first_free_ports(tmp_path, host):
    cfg = make(tmp_path, {"ssh": 0, "vnc": 0})
    assert podman.pick_auto_ports(cfg, None) == {"ssh": 2222, "vnc": 5901}

    host["busy"] = {2222, 5901}
    host["containers"] = [ContainerInfo("stopped", "exited", "Exited", {}, [PortMap(2223, 22, "tcp")])]
    cfg = make(tmp_path, {"ssh": 0, "vnc": 0}, [{"host": 2224, "container": 80}])
    assert podman.pick_auto_ports(cfg, None) == {"ssh": 2225, "vnc": 5902}


def test_only_zero_ports_are_picked(tmp_path, host):
    assert podman.pick_auto_ports(make(tmp_path, {"ssh": 2400, "vnc": 5990}), None) == {}
    cfg = make(tmp_path, {"ssh": 0, "vnc": 2222})  # a fixed port is not picked for the other one
    assert podman.pick_auto_ports(cfg, None) == {"ssh": 2223}


def test_existing_container_keeps_its_ports(tmp_path, host):
    own = ContainerInfo("mine", "running", "Up", {}, [PortMap(2230, 22, "tcp"), PortMap(5910, 5901, "tcp")])
    host["busy"] = {2230, 5910}  # held by the container itself
    cfg = make(tmp_path, {"ssh": 0, "vnc": 0})
    assert podman.pick_auto_ports(cfg, own) == {"ssh": 2230, "vnc": 5910}


def test_save_ports_keeps_the_rest_of_the_file(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        '[container]\nname = "x"\nssh = 0   # not in [ports]\n\n'
        '[ports]\nbind_address = "0.0.0.0"\nssh = 0                 # -> container 22\nvnc = 0\n\n'
        '[vnc]\ngeometry = "1920x1080"\n'
    )
    save_ports(path, {"ssh": 2223, "vnc": 5902})
    assert path.read_text() == (
        '[container]\nname = "x"\nssh = 0   # not in [ports]\n\n'
        '[ports]\nbind_address = "0.0.0.0"\nssh = 2223                 # -> container 22\nvnc = 5902\n\n'
        '[vnc]\ngeometry = "1920x1080"\n'
    )
    with pytest.raises(CasperError, match="ports.vnc = 0"):
        save_ports(path, {"vnc": 5903})


# ---- up ----

@pytest.fixture
def up_env(tmp_path, monkeypatch, host):
    cfg = tmp_path / "casper-dev.toml"
    cfg.write_text(f'os = "{DEV_DEFAULT_OS}"\n[container]\nname = "t1"\n[ports]\nssh = 0   # auto\nvnc = 0\n')
    runs: list[list[str]] = []
    monkeypatch.setattr(podman, "check_dependencies", lambda: None)
    monkeypatch.setattr(podman, "get_container", lambda name: None)
    monkeypatch.setattr(podman, "image_state", lambda cfg: "current")
    monkeypatch.setattr(podman, "check_image_matches", lambda cfg: None)
    monkeypatch.setattr(podman, "run", lambda cmd, **k: runs.append(cmd))
    monkeypatch.chdir(tmp_path)
    host["busy"] = {2222}
    return cfg, runs


def test_up_picks_and_saves_ports(up_env):
    cfg, runs = up_env
    r = CliRunner().invoke(cli.dev_app, ["up"])
    assert r.exit_code == 0, r.output
    assert "Picked free host port(s) ssh 2223, vnc 5901; saved in casper-dev.toml" in r.output
    assert "ssh = 2223   # auto\nvnc = 5901\n" in cfg.read_text()
    assert load_config(cfg, DEV).ssh_port == 2223
    run = runs[-1]
    assert "0.0.0.0:2223:22/tcp" in run and "0.0.0.0:5901:5901/tcp" in run
    assert "ssh -p 2223" in r.output and ":5901" in r.output


def test_config_shows_auto_ports(up_env):
    r = CliRunner().invoke(cli.dev_app, ["config"])
    assert r.exit_code == 0, r.output
    assert "ssh auto (first free from 2222, picked by up), vnc auto (first free from 5901, picked by up)" in r.output
