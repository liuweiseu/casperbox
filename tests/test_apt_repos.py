"""basebox.toml [[apt_repos]]: parsing, the APT_REPOS build arg and dev/add_apt_repos.sh."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from casperbox import cli
from casperbox.config import DEV_DEFAULT_OS, AptRepo, parse_dev_config
from casperbox.errors import CasperError
from casperbox.podman import build_args
from casperbox.versions import assets_dir

YAZI = {
    "name": "yazi",
    "key": "https://yazi-rs.github.io/builds/yazi-keyring.gpg",
    "url": "https://yazi-rs.github.io/builds/",
    "suite": "stable",
}
SCRIPT = assets_dir() / "dev" / "add_apt_repos.sh"


def make(tmp_path: Path, *repos: dict, **doc):
    return parse_dev_config({"os": DEV_DEFAULT_OS, "apt_repos": list(repos), **doc}, tmp_path / "basebox.toml")


def test_parse_defaults(tmp_path):
    (repo,) = make(tmp_path, YAZI, packages=["yazi"]).apt_repos
    assert repo == AptRepo("yazi", YAZI["key"], YAZI["url"], "stable", ("main",), "")
    assert make(tmp_path).apt_repos == []
    (docker,) = make(tmp_path, {"name": "docker", "key": "https://download.docker.com/linux/ubuntu/gpg",
                                "url": "https://download.docker.com/linux/ubuntu", "components": ["stable"],
                                "key_fingerprint": "9DC8 5822 9FC7 DD38 854A  E2D8 8D81 803C 0EBF CD88"}).apt_repos
    assert docker.suite == "" and docker.components == ("stable",)
    assert docker.key_fingerprint == "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
    (flat,) = make(tmp_path, {**YAZI, "suite": "./"}).apt_repos
    assert flat.components == ()


@pytest.mark.parametrize("change,error", [
    ({"name": "Yazi"}, r"apt_repos\[0\].name 'Yazi'"),
    ({"name": "ubuntu"}, "is used by the image"),
    ({"key": "http://yazi-rs.github.io/builds/yazi-keyring.gpg"}, r"key must be an https:// URL"),
    ({"url": "ftp://example.com/repo"}, r"url must be an http\(s\)://"),
    ({"url": "https://example.com/a|b"}, r"url must be an http\(s\)://"),
    ({"suite": "stable main"}, "suite 'stable main' is not valid"),
    ({"components": []}, "components must not be empty"),
    ({"components": ["main contrib"]}, "components: invalid entry"),
    ({"suite": "./", "components": ["main"]}, "flat repository"),
    ({"key_fingerprint": "1234"}, "key_fingerprint must be 40 hex digits"),
    ({"arch": "amd64"}, r"Unknown key\(s\) in apt_repos\[0\]: arch"),
])
def test_parse_errors(tmp_path, change, error):
    with pytest.raises(CasperError, match=error):
        make(tmp_path, {**YAZI, **change})


def test_name_used_twice(tmp_path):
    with pytest.raises(CasperError, match="used twice"):
        make(tmp_path, YAZI, YAZI)


def test_build_arg(tmp_path):
    cfg = make(tmp_path, YAZI, {**YAZI, "name": "two", "suite": "", "components": ["main", "contrib"],
                                "key_fingerprint": "a" * 40})
    b = build_args(cfg, Path("/ctx"))
    assert ("APT_REPOS=yazi|https://yazi-rs.github.io/builds/yazi-keyring.gpg|https://yazi-rs.github.io/builds/|stable|main|"
            " two|https://yazi-rs.github.io/builds/yazi-keyring.gpg|https://yazi-rs.github.io/builds/||main,contrib|"
            + "A" * 40) in b
    assert "APT_REPOS=" in build_args(make(tmp_path), Path("/ctx"))


def test_config_command_lists_repos(tmp_path, monkeypatch):
    path = tmp_path / "basebox.toml"
    path.write_text(f'os = "{DEV_DEFAULT_OS}"\npackages = ["yazi"]\n[[apt_repos]]\nname = "yazi"\n'
                    f'key = "{YAZI["key"]}"\nurl = "{YAZI["url"]}"\nsuite = "stable"\n')
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli.dev_app, ["config"])
    assert r.exit_code == 0, r.output
    assert "yazi: https://yazi-rs.github.io/builds/ stable main" in r.output


# ---- dev/add_apt_repos.sh, run against a fake /etc with file:// keys ----

@pytest.fixture
def gpg_key(tmp_path):
    """A throwaway OpenPGP key: (armored file, binary file, fingerprint)."""
    if not shutil.which("gpg"):
        pytest.skip("gpg not installed")
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    gpg = ["gpg", "--batch", "--quiet"]
    subprocess.run([*gpg, "--passphrase", "", "--quick-gen-key", "Test <test@example.com>", "ed25519", "sign", "never"],
                   env=env, check=True, capture_output=True)
    out = subprocess.run([*gpg, "--with-colons", "--list-keys"], env=env, check=True, capture_output=True, text=True)
    fpr = next(line.split(":")[9] for line in out.stdout.splitlines() if line.startswith("fpr"))
    asc, binary = tmp_path / "key.asc", tmp_path / "key.gpg"
    subprocess.run([*gpg, "--armor", "--output", str(asc), "--export", fpr], env=env, check=True)
    subprocess.run([*gpg, "--output", str(binary), "--export", fpr], env=env, check=True)
    return asc, binary, fpr


def run_script(root: Path, repos: str):
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "os-release").write_text("ID=ubuntu\nVERSION_CODENAME=noble\n")
    env = {**os.environ, "APT_ROOT": str(root), "APT_REPOS": repos}
    return subprocess.run(["bash", str(SCRIPT)], check=False, env=env, capture_output=True, text=True)


def test_script_without_repos(tmp_path):
    r = run_script(tmp_path / "root", "")
    assert r.returncode == 0 and "No extra apt repositories" in r.stdout


def test_script_writes_sources_and_keys(tmp_path, gpg_key):
    asc, binary, fpr = gpg_key
    root = tmp_path / "root"
    r = run_script(root, f"one|file://{asc}|https://example.com/apt|stable|main,contrib|{fpr} "
                         f"two|file://{binary}|https://example.com/ubuntu||stable| "
                         f"flat|file://{binary}|https://example.com/flat|./||")
    assert r.returncode == 0, r.stderr
    apt = root / "etc" / "apt"
    assert (apt / "keyrings" / "one.asc").read_bytes() == asc.read_bytes()
    assert (apt / "keyrings" / "two.gpg").read_bytes() == binary.read_bytes()
    assert (apt / "sources.list.d" / "one.sources").read_text() == (
        "# [[apt_repos]] one in basebox.toml\nTypes: deb\nURIs: https://example.com/apt\n"
        "Suites: stable\nComponents: main contrib\nSigned-By: /etc/apt/keyrings/one.asc\n"
    )
    two = (apt / "sources.list.d" / "two.sources").read_text()
    assert "Suites: noble\nComponents: stable\nSigned-By: /etc/apt/keyrings/two.gpg\n" in two  # codename
    flat = (apt / "sources.list.d" / "flat.sources").read_text()
    assert "Suites: ./\nSigned-By:" in flat and "Components" not in flat
    assert f"key fingerprint {fpr} OK" in r.stdout


def test_script_rejects_wrong_fingerprint(tmp_path, gpg_key):
    asc, _, fpr = gpg_key
    root = tmp_path / "root"
    r = run_script(root, f"one|file://{asc}|https://example.com/apt|stable|main|{'0' * 40}")
    assert r.returncode == 1
    assert "fingerprint mismatch" in r.stderr and fpr in r.stderr
    assert not (root / "etc" / "apt" / "keyrings" / "one.asc").exists()
    assert not (root / "etc" / "apt" / "sources.list.d" / "one.sources").exists()


def test_script_reports_key_download_failure(tmp_path):
    r = run_script(tmp_path / "root", f"one|file://{tmp_path}/missing.gpg|https://example.com/apt|stable|main|")
    assert r.returncode == 1 and "cannot download the signing key" in r.stderr
