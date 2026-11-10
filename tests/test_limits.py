"""container.memory / cpus / cpuset: parsing, podman run options and the cgroup check."""

import json
from pathlib import Path

import pytest

from casperbox import podman
from casperbox.config import DEV_DEFAULT_OS, cpuset_cores, parse_dev_config
from casperbox.errors import CasperError


def make(tmp_path: Path, **container):
    return parse_dev_config({"os": DEV_DEFAULT_OS, "container": container}, tmp_path / "casper-dev.toml")


def test_no_limits_by_default(tmp_path):
    cfg = make(tmp_path)
    assert (cfg.memory, cfg.cpus, cfg.cpuset) == ("", 0, "")
    assert podman.limit_args(cfg) == []
    args = podman.run_args(cfg)
    assert not any(a in args for a in ("--memory", "--memory-swap", "--cpus", "--cpuset-cpus"))


def test_limits_in_run_args(tmp_path):
    cfg = make(tmp_path, memory="64G", cpus=16, cpuset="0-7, 16-23")
    assert podman.limit_args(cfg) == [
        "--memory", "64g", "--memory-swap", "64g", "--cpus", "16", "--cpuset-cpus", "0-7,16-23",
    ]
    args = podman.run_args(cfg)
    assert args[args.index("--memory") + 1] == "64g"
    assert make(tmp_path, cpus=8.5).cpus == 8.5
    assert podman.limit_args(make(tmp_path, cpus=8.5)) == ["--cpus", "8.5"]


@pytest.mark.parametrize("key,value", [
    ("memory", "64"), ("memory", "0g"), ("memory", "64 GB"), ("memory", 64),
    ("cpus", -1), ("cpus", "8"), ("cpus", True),
    ("cpuset", "0-"), ("cpuset", "a"), ("cpuset", "7-3"),
])
def test_invalid_limits(tmp_path, key, value):
    with pytest.raises(CasperError, match=f"container.{key}"):
        make(tmp_path, **{key: value})


def test_cpuset_cores():
    assert cpuset_cores("0-3,8") == [0, 1, 2, 3, 8]
    assert cpuset_cores("") == []


@pytest.fixture
def podman_info(monkeypatch):
    """Fake `podman info`: set .version and .controllers; records whether it was called."""
    state = {"version": "v2", "controllers": ["cpu", "cpuset", "memory", "pids"], "called": False}

    def output(cmd, check=True):
        state["called"] = True
        fmt = cmd[-1]
        if "CgroupsVersion" in fmt:
            return state["version"] + "\n"
        if "CgroupControllers" in fmt:
            return json.dumps(state["controllers"])
        raise AssertionError(cmd)

    monkeypatch.setattr(podman, "_output", output)
    monkeypatch.setattr(podman.os, "cpu_count", lambda: 32)
    return state


def test_check_limits_without_limits_does_not_ask_podman(tmp_path, podman_info):
    podman_info["version"] = "v1"
    podman.check_limits(make(tmp_path))
    assert podman_info["called"] is False


def test_check_limits_ok(tmp_path, podman_info):
    podman.check_limits(make(tmp_path, memory="64g", cpus=16, cpuset="0-31"))


def test_check_limits_needs_cgroup_v2(tmp_path, podman_info):
    podman_info["version"] = "v1"
    with pytest.raises(CasperError, match="container.memory need cgroup v2"):
        podman.check_limits(make(tmp_path, memory="64g"))


def test_check_limits_missing_controller(tmp_path, podman_info):
    podman_info["controllers"] = ["memory", "pids"]
    podman.check_limits(make(tmp_path, memory="64g"))  # memory is delegated
    with pytest.raises(CasperError, match=r"container.cpuset need the cgroup controller\(s\) cpuset") as e:
        podman.check_limits(make(tmp_path, memory="64g", cpuset="0-3"))
    assert "Delegate=cpu cpuset io memory pids" in str(e.value)


def test_check_limits_more_cpus_than_host(tmp_path, podman_info):
    with pytest.raises(CasperError, match="only 32 CPUs"):
        podman.check_limits(make(tmp_path, cpus=64))
    with pytest.raises(CasperError, match="only CPUs 0-31"):
        podman.check_limits(make(tmp_path, cpuset="16-47"))


def test_container_limits(monkeypatch):
    monkeypatch.setattr(podman, "_output", lambda cmd, check=True: f"{64 * 2**30}|16000000000|0-15\n")
    assert podman.container_limits("c") == {"memory": "64g", "cpus": "16", "cpuset": "0-15"}
    monkeypatch.setattr(podman, "_output", lambda cmd, check=True: "0|0|\n")
    assert podman.container_limits("c") == {}
