"""`commit` saves a container as an image, optionally to a tar file (podman calls are mocked)."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from casperbox import cli, podman
from casperbox.config import DEV_DEFAULT_OS
from casperbox.errors import CasperError


@pytest.fixture
def cmds(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "casper-dev.toml"
    cfg.write_text(f'os = "{DEV_DEFAULT_OS}"\n[container]\nname = "Dev1"\n')
    existing = podman.ContainerInfo(
        "Dev1", "running", "Up", {podman.LABEL_CONFIG: str(cfg.resolve()), podman.LABEL_KIND: "dev"}, []
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(podman, "check_dependencies", lambda: None)
    monkeypatch.setattr(podman, "get_container", lambda name: existing)
    monkeypatch.setattr(podman, "run", lambda cmd, **k: calls.append(cmd))
    monkeypatch.chdir(tmp_path)
    return calls


def invoke(*args):
    return CliRunner().invoke(cli.dev_app, ["commit", *args])


def test_commit_default_name(cmds):
    r = invoke()
    assert r.exit_code == 0, r.output
    (cmd,) = cmds
    assert cmd[:2] == ["podman", "commit"] and cmd[-2] == "Dev1"
    assert re.fullmatch(r"localhost/dev1:snapshot-\d{8}-\d{6}", cmd[-1])
    assert "passwords" in r.output


def test_commit_named_image_and_tar(cmds, tmp_path):
    r = invoke("myimg:v1", "-o", "out.tar")
    assert r.exit_code == 0, r.output
    assert cmds[0][-1] == "myimg:v1"
    assert cmds[1] == ["podman", "save", "-o", str(tmp_path / "out.tar"), "myimg:v1"]


def test_commit_does_not_overwrite_tar(cmds, tmp_path):
    (tmp_path / "out.tar").write_text("old")
    r = invoke("-o", "out.tar")
    assert isinstance(r.exception, CasperError) and "--force" in str(r.exception)
    assert cmds == []
    r = invoke("-o", "out.tar", "--force")
    assert r.exit_code == 0, r.output
    assert [c[1] for c in cmds] == ["commit", "save"]


def test_commit_needs_a_container(cmds, monkeypatch):
    monkeypatch.setattr(podman, "get_container", lambda name: None)
    r = invoke()
    assert isinstance(r.exception, CasperError) and "up" in str(r.exception)


def test_commit_clears_casperbox_labels():
    cmd = podman.commit_args("c1", "img:1")
    changes = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--change"]
    assert f"LABEL {podman.LABEL_BUILD_HASH}=" in changes
    assert f"LABEL {podman.LABEL_MANAGED}=" in changes
    assert f"LABEL {podman.LABEL_SNAPSHOT_OF}=c1" in changes
