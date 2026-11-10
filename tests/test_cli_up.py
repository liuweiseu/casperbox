"""`up` asks before recreating an existing container; `restart` keeps it (podman calls are mocked)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from casperbox import cli, podman
from casperbox.config import DEV_DEFAULT_OS
from casperbox.errors import CasperError


@pytest.fixture
def fake_podman(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "casper-dev.toml"
    cfg.write_text(f'os = "{DEV_DEFAULT_OS}"\n[container]\nname = "t1"\n')
    calls: list[str] = []
    existing = podman.ContainerInfo(
        "t1", "running", "Up", {podman.LABEL_CONFIG: str(cfg.resolve()), podman.LABEL_KIND: "dev"}, []
    )
    monkeypatch.setattr(podman, "check_dependencies", lambda: None)
    monkeypatch.setattr(podman, "get_container", lambda name: existing if "removed" not in calls else None)
    monkeypatch.setattr(podman, "check_ports_free", lambda cfg, own: None)
    monkeypatch.setattr(podman, "image_state", lambda cfg: "current")
    monkeypatch.setattr(podman, "check_image_matches", lambda cfg: None)
    monkeypatch.setattr(podman, "remove_container", lambda name: calls.append("removed"))
    monkeypatch.setattr(
        podman, "run", lambda cmd, **k: calls.append("restarted" if cmd[:2] == ["podman", "restart"] else "created")
    )
    monkeypatch.chdir(tmp_path)
    return calls


def invoke(*args, tty: bool, answer: str = "", monkeypatch=None):
    monkeypatch.setattr(cli, "stdin_is_tty", lambda: tty)
    return CliRunner().invoke(cli.dev_app, list(args), input=answer)


def test_existing_container_not_recreated_without_confirmation(fake_podman, monkeypatch):
    command = "up"
    r = invoke(command, tty=True, answer="n\n", monkeypatch=monkeypatch)
    assert r.exit_code == 1
    assert "Aborted, nothing was changed" in r.output and fake_podman == []
    assert "already exists (running)" in r.output and "are lost" in r.output

    r = invoke(command, tty=False, monkeypatch=monkeypatch)
    assert isinstance(r.exception, CasperError) and "--yes" in str(r.exception)
    assert fake_podman == []


def test_existing_container_recreated_when_confirmed(fake_podman, monkeypatch):
    r = invoke("up", tty=True, answer="y\n", monkeypatch=monkeypatch)
    assert r.exit_code == 0, r.output
    assert fake_podman == ["removed", "created"]


def test_yes_skips_the_question(fake_podman, monkeypatch):
    r = invoke("up", "--yes", tty=False, monkeypatch=monkeypatch)
    assert r.exit_code == 0, r.output
    assert fake_podman == ["removed", "created"]


def test_restart_keeps_the_container(fake_podman, monkeypatch):
    r = invoke("restart", tty=False, monkeypatch=monkeypatch)
    assert r.exit_code == 0, r.output
    assert fake_podman == ["restarted"]  # not removed, no question asked


def test_restart_needs_an_existing_container(fake_podman, monkeypatch):
    monkeypatch.setattr(podman, "get_container", lambda name: None)
    r = invoke("restart", tty=False, monkeypatch=monkeypatch)
    assert isinstance(r.exception, CasperError) and "up" in str(r.exception)
    assert fake_podman == []
