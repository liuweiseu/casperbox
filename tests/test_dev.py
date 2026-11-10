import os
import tomllib
from pathlib import Path

import pytest

from casperbox.config import (
    DEV_DEFAULT_OS,
    DEV_TESTED_OS,
    normalize_image,
    os_slug,
    parse_dev_config,
    render_dev_template,
)
from casperbox.errors import CasperError
from casperbox.podman import build_args, container_env, run_args


@pytest.fixture
def dev(tmp_path: Path):
    cfg_path = tmp_path / "proj" / "casper-dev.toml"
    cfg_path.parent.mkdir()

    def make(**doc):
        doc.setdefault("os", DEV_DEFAULT_OS)
        return parse_dev_config(doc, cfg_path)

    make.tmp = tmp_path
    return make


@pytest.mark.parametrize(
    ("image", "full"),
    [
        ("ubuntu:26.04", "docker.io/library/ubuntu:26.04"),
        ("myorg/img:1.0", "docker.io/myorg/img:1.0"),
        ("quay.io/org/image:tag", "quay.io/org/image:tag"),
        ("localhost/img:dev", "localhost/img:dev"),
        ("registry.example.com:5000/a/b:v1", "registry.example.com:5000/a/b:v1"),
    ],
)
def test_normalize_image(image, full):
    assert normalize_image(image) == full


@pytest.mark.parametrize("image", ["abc", "ubuntu", "Ubuntu:26.04", "ubuntu:", "ubuntu:26.04 ; rm", ":latest"])
def test_normalize_image_rejects(image):
    with pytest.raises(CasperError, match="Invalid os"):
        normalize_image(image)


def test_os_slug():
    assert os_slug("ubuntu:26.04") == "ubuntu-26.04"
    assert os_slug("quay.io/org/image:tag") == "image-tag"


def test_template_parses():
    for image in (*DEV_TESTED_OS, "debian:13"):
        doc = tomllib.loads(render_dev_template(image))
        assert doc["os"] == image
        assert doc["packages"] == []
        assert doc["container"]["name"] == f"casper-dev-{os_slug(image)}"
        assert (doc["ports"]["ssh"], doc["ports"]["vnc"]) == (2322, 5902)


def test_defaults(dev):
    cfg = dev()
    assert cfg.name == "casper-dev-ubuntu-26.04"
    assert cfg.image == "casperbox-dev:casper-dev-ubuntu-26.04"
    assert cfg.base_image == "docker.io/library/ubuntu:26.04"
    assert (cfg.ssh_port, cfg.vnc_port) == (2322, 5902)
    assert cfg.mounts == [] and cfg.packages == []
    assert cfg.vivado_dir is None and cfg.work_dir is None
    assert cfg.warnings == []
    assert cfg.tool == "casperbox-dev"


def test_untested_os_warns(dev):
    assert "has not been tested" in dev(os="debian:13").warnings[0]


def test_release_only_keys_rejected(dev):
    with pytest.raises(CasperError, match="Unknown key.*vivado"):
        dev(vivado={"dir": "/tools/Xilinx"})
    with pytest.raises(CasperError, match="Unknown key.*version"):
        dev(version="m2021a")


def test_packages(dev):
    cfg = dev(packages=["git", "vim", "git", "python3-pip", "libfoo:amd64", "bar=1.2-3ubuntu1"])
    assert cfg.packages == ["git", "vim", "python3-pip", "libfoo:amd64", "bar=1.2-3ubuntu1"]
    with pytest.raises(CasperError, match="Invalid entry in packages"):
        dev(packages=["git; rm -rf /"])
    with pytest.raises(CasperError, match="Invalid entry in packages"):
        dev(packages=[1])
    with pytest.raises(CasperError, match="must be a list"):
        dev(packages="git vim")


def test_build_and_run_args(dev):
    (dev.tmp / "src").mkdir()
    cfg = dev(
        packages=["git", "vim"],
        mounts=[{"host": "../src", "container": "~/src"}],
        container={"name": "lab", "password": "s3cret-pw"},
    )
    b = build_args(cfg)
    assert "BASE_IMAGE=docker.io/library/ubuntu:26.04" in b
    assert "PACKAGES=git vim" in b
    assert b[b.index("-t") + 1] == "casperbox-dev:lab"
    assert b[b.index("-f") + 1].endswith("assets/dev/Dockerfile")

    r = run_args(cfg)
    volumes = [r[i + 1] for i, a in enumerate(r) if a == "-v"]
    assert volumes == [f"{dev.tmp / 'src'}:/home/casper/src:rw"]  # nothing mounted by default
    assert "casperbox.kind=dev" in r and "casperbox.os=ubuntu:26.04" in r
    assert "VIVADO_DIR" not in container_env(cfg)
    assert not any("s3cret-pw" in a for a in r)


def test_dev_mounts_must_exist_and_be_readable(dev):
    with pytest.raises(CasperError, match="does not exist"):
        dev(mounts=[{"host": "../missing", "container": "~/m"}])
    (dev.tmp / "locked").mkdir()
    (dev.tmp / "locked").chmod(0o300)
    try:
        cfg = dev(mounts=[{"host": "../locked", "container": "~/m"}])
    finally:
        (dev.tmp / "locked").chmod(0o700)
    if os.geteuid() != 0:
        assert any("no read permission" in w for w in cfg.warnings)


def test_dev_apt_mirror(dev):
    assert dev().apt_mirror == ""
    cfg = dev(build={"apt_mirror": "https://mirrors.ocf.berkeley.edu/ubuntu"})
    assert "APT_MIRROR=https://mirrors.ocf.berkeley.edu/ubuntu" in build_args(cfg)
