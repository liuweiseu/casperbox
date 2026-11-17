"""casperbox-dev: [[tools]], container.gpus / rdma, network.mode = "host" and interface names."""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from casperbox import podman
from casperbox.config import (
    DEV_DEFAULT_OS,
    parse_config,
    parse_dev_config,
    render_dev_template,
)
from casperbox.errors import CasperError
from casperbox.versions import assets_dir

INSTALL_TOOLS = assets_dir() / "dev" / "install_tools.sh"


@pytest.fixture
def make(tmp_path: Path):
    cfg_path = tmp_path / "proj" / "casperbox-dev.toml"
    cfg_path.parent.mkdir()

    def _make(**doc):
        doc.setdefault("os", DEV_DEFAULT_OS)
        return parse_dev_config(doc, cfg_path)

    return _make


# ---- casperbox (toolflow) does not have these keys ----

@pytest.mark.parametrize("doc", [
    {"container": {"gpus": "0"}},
    {"container": {"rdma": "all"}},
    {"network": {"mode": "host"}},
    {"tools": [{"name": "n", "script": "x"}]},
])
def test_casperbox_rejects_dev_only_keys(tmp_path, doc):
    vivado = tmp_path / "Xilinx"
    vivado.mkdir()
    base = {"version": "m2021a", "vivado": {"dir": str(vivado)}, "paths": {"license_dir": str(tmp_path)}}
    for key, value in doc.items():
        base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
    with pytest.raises(CasperError, match="Unknown key"):
        parse_config(base, tmp_path / "casperbox.toml")


def test_casperbox_bind_address_must_be_an_ip(tmp_path):
    vivado = tmp_path / "Xilinx"
    vivado.mkdir()
    doc = {"version": "m2021a", "vivado": {"dir": str(vivado)}, "paths": {"license_dir": str(tmp_path)},
           "ports": {"bind_address": "eno1"}}
    with pytest.raises(CasperError, match="Invalid ports.bind_address"):
        parse_config(doc, tmp_path / "casperbox.toml")


# ---- [[tools]] ----

def test_tools(make):
    cfg = make(tools=[
        {"name": "uv", "version": "0.9.0", "script": "echo uv"},
        {"name": "dotfiles", "run_as": "user", "script": "echo dot\n"},
    ])
    assert [(t.name, t.version, t.run_as) for t in cfg.tools] == [("uv", "0.9.0", "root"), ("dotfiles", "", "user")]
    assert all(t.script.endswith("\n") for t in cfg.tools)
    assert make().tools == []


@pytest.mark.parametrize(("tool", "match"), [
    ({"name": "Node", "script": "x"}, "name"),
    ({"name": "a b", "script": "x"}, "name"),
    ({"name": "n", "script": "  \n"}, "must not be empty"),
    ({"name": "n"}, "script is required"),
    ({"name": "n", "script": "x", "run_as": "admin"}, "run_as"),
    ({"name": "n", "script": "x", "version": "1; rm"}, "version"),
    ({"name": "n", "script": "x", "extra": 1}, "Unknown key"),
])
def test_invalid_tools(make, tool, match):
    with pytest.raises(CasperError, match=match):
        make(tools=[tool])


def test_duplicate_tool_names(make):
    with pytest.raises(CasperError, match="used twice"):
        make(tools=[{"name": "n", "script": "x"}, {"name": "n", "script": "y"}])


def test_tool_files_keep_the_config_order(make):
    cfg = make(tools=[
        {"name": "b", "script": "echo b", "version": "1"},
        {"name": "a", "script": "echo a", "run_as": "user"},
        {"name": "c", "script": "echo c"},
    ])
    files = podman.tool_files(cfg)
    assert files["tools/root/00-b.sh"] == b"#!/usr/bin/env bash\nset -euo pipefail\necho b\n"
    assert files["tools/root/00-b.version"] == b"1"
    assert "tools/user/01-a.sh" in files and "tools/root/02-c.sh" in files


def test_tool_change_makes_the_image_outdated(make):
    one = make(tools=[{"name": "n", "script": "echo 1"}])
    assert podman.build_hash(one) != podman.build_hash(make(tools=[{"name": "n", "script": "echo 2"}]))
    assert podman.build_hash(one) != podman.build_hash(make(tools=[{"name": "n", "script": "echo 1", "version": "2"}]))
    assert podman.build_hash(one) == podman.build_hash(make(tools=[{"name": "n", "script": "echo 1"}]))


def test_build_context_has_both_tool_directories(make):
    cfg = make(tools=[{"name": "n", "script": "echo 1"}])
    with podman.build_context(cfg) as ctx:
        assert (ctx / "dev" / "Dockerfile").is_file() and (ctx / "dev" / "install_tools.sh").is_file()
        assert (ctx / "tools" / "root" / "00-n.sh").is_file()
        assert (ctx / "tools" / "user").is_dir()  # COPY tools/user/ needs it even without user tools
    with podman.build_context(make()) as ctx:
        assert sorted(p.name for p in (ctx / "tools").iterdir()) == ["root", "user"]


@pytest.mark.skipif(not shutil.which("dpkg"), reason="needs dpkg")
def test_install_tools_script_runs_root_tools_in_order(make, tmp_path):
    cfg = make(tools=[
        {"name": "b", "version": "1.2", "script": 'echo "b $VERSION $DEB_ARCH $CONTAINER_USER" >> "$OUT"'},
        {"name": "a", "script": 'echo a >> "$OUT"'},
        {"name": "u", "run_as": "user", "script": "echo user"},
    ])
    tools = tmp_path / "ctx"
    for name, data in podman.tool_files(cfg).items():
        (tools / name).parent.mkdir(parents=True, exist_ok=True)
        (tools / name).write_bytes(data)
    out = tmp_path / "out.txt"
    env = {**os.environ, "CONTAINER_USER": "casper", "OUT": str(out)}
    r = subprocess.run(["bash", str(INSTALL_TOOLS), "root", str(tools / "tools" / "root")],
                       env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, check=True).stdout.strip()
    assert out.read_text() == f"b 1.2 {arch} casper\na\n"
    assert "==> tool b (root, version 1.2)" in r.stdout

    (tools / "tools" / "root" / "01-a.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\nfalse\necho no\n")
    r = subprocess.run(["bash", str(INSTALL_TOOLS), "root", str(tools / "tools" / "root")],
                       env=env, capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "no" not in r.stdout


def test_template_tools_example_works_when_uncommented():
    text = render_dev_template(DEV_DEFAULT_OS)
    assert "tools" not in tomllib.loads(text)  # commented out by default
    start = text.index("# [[tools]]")
    end = text.index("# ---", start)
    block = re.sub(r"^# ?", "", text[start:end], flags=re.MULTILINE)
    doc = tomllib.loads(text[:start] + block)
    cfg = parse_dev_config(doc, Path("/tmp/casperbox-dev.toml"))
    assert [(t.name, t.run_as) for t in cfg.tools] == [("lazygit", "root"), ("shell-aliases", "user")]
    assert cfg.tools[0].version and '"$DEB_ARCH"' in cfg.tools[0].script and "${VERSION}" in cfg.tools[0].script
    subprocess.run(["bash", "-n"], input=cfg.tools[0].script, text=True, check=True)


# ---- container.gpus ----

@pytest.mark.parametrize(("raw", "gpus"), [
    ("", []), ("0", ["0"]), ("0,1", ["0", "1"]), ("0, 1", ["0", "1"]), ("1,1,2", ["1", "2"]), ("all", ["all"]),
])
def test_gpus(make, raw, gpus):
    assert make(container={"gpus": raw}).gpus == gpus


@pytest.mark.parametrize("raw", ["all,0", "gpu0", "-1", "0;1"])
def test_invalid_gpus(make, raw):
    with pytest.raises(CasperError, match="container.gpus"):
        make(container={"gpus": raw})


def test_gpu_run_args(make):
    r = podman.run_args(make(container={"gpus": "0,1"}))
    assert [r[i + 1] for i, a in enumerate(r) if a == "--device"] == ["nvidia.com/gpu=0", "nvidia.com/gpu=1"]
    assert "nofile=65535:65535" in r
    plain = podman.run_args(make())
    assert "--device" not in plain and "--ulimit" not in plain


def test_check_gpus(make, monkeypatch):
    podman.check_gpus(make())  # no GPUs: nothing to check
    monkeypatch.setattr(podman.shutil, "which", lambda name: "/usr/bin/nvidia-ctk")
    monkeypatch.setattr(podman, "_output", lambda cmd, check=True: "nvidia.com/gpu=0\nnvidia.com/gpu=all\n")
    podman.check_gpus(make(container={"gpus": "0"}))
    podman.check_gpus(make(container={"gpus": "all"}))
    with pytest.raises(CasperError, match=r"nvidia.com/gpu=3"):
        podman.check_gpus(make(container={"gpus": "0,3"}))
    monkeypatch.setattr(podman.shutil, "which", lambda name: None)
    with pytest.raises(CasperError, match="nvidia-ctk is not installed"):
        podman.check_gpus(make(container={"gpus": "0"}))


# ---- network.mode and ports.bind_address ----

def test_network_defaults_and_validation(make):
    cfg = make()
    assert cfg.network_mode == "pasta" and not cfg.host_network and cfg.rdma == [] and cfg.gpus == []
    assert podman.pasta_network(cfg).startswith("pasta:")
    with pytest.raises(CasperError, match="network.mode"):
        make(network={"mode": "bridge"})
    with pytest.raises(CasperError, match="Unknown key"):
        make(network={"mode": "host", "x": 1})


def test_bind_address_can_be_an_interface_name(make, monkeypatch):
    cfg = make(ports={"bind_address": "eno1", "ssh": 2222, "vnc": 5901})
    assert cfg.bind_name == "eno1"
    monkeypatch.setattr(podman, "interface_address", lambda name: "10.1.2.3")
    podman.resolve_bind_address(cfg)
    assert cfg.bind_address == "10.1.2.3"
    assert "10.1.2.3:2222:22/tcp" in podman.run_args(cfg)
    assert make(ports={"bind_address": "192.168.1.5"}).bind_name == ""
    for bad in ("eth 0", "a/b", "x" * 16, ""):
        with pytest.raises(CasperError, match="bind_address"):
            make(ports={"bind_address": bad})


def test_unknown_interface_is_an_error():
    with pytest.raises(CasperError, match="no such network interface"):
        podman.interface_address("casper-no-nic")


def test_host_mode_run_args_env_and_label(make):
    cfg = make(network={"mode": "host"}, ports={"bind_address": "10.0.0.5", "ssh": 2200, "vnc": 5950})
    r = podman.run_args(cfg)
    assert r[r.index("--network") + 1] == "host"
    assert "-p" not in r  # nothing is published: the services listen on the host themselves
    assert f"{podman.LABEL_HOST_PORTS}=2200:22/tcp,5950:5901/tcp" in r
    env = podman.container_env(cfg)
    assert (env["CASPERBOX_SSH_PORT"], env["CASPERBOX_VNC_PORT"], env["CASPERBOX_BIND"]) == ("2200", "5950", "10.0.0.5")
    plain = make(ports={"ssh": 2200, "vnc": 5950})
    assert "CASPERBOX_BIND" not in podman.container_env(plain)
    assert not any(a.startswith(podman.LABEL_HOST_PORTS) for a in podman.run_args(plain))


def test_host_mode_warnings(make):
    w = make(network={"mode": "host"}, container={"mac": "02:00:00:00:00:01"},
             extra_ports=[{"host": 8888, "container": 8888}]).warnings
    assert any("container.mac is ignored" in x for x in w) and any("extra_ports" in x for x in w)
    assert make(network={"mode": "host"}).warnings == []  # bind_address 0.0.0.0 is fine
    assert make(container={"mac": "02:00:00:00:00:01"}).warnings == []


def test_host_mode_ignores_extra_ports_in_the_port_check(make, monkeypatch):
    monkeypatch.setattr(podman, "list_containers", lambda name=None: [])
    seen = []
    monkeypatch.setattr(podman, "_port_free", lambda addr, port, proto: seen.append(port) or True)
    cfg = make(network={"mode": "host"}, ports={"ssh": 2200, "vnc": 5950},
               extra_ports=[{"host": 8888, "container": 8888}])
    podman.check_ports_free(cfg, None)
    assert seen == [2200, 5950]


def test_host_network_ports_come_from_the_label(monkeypatch):
    rows = ('[{"Names": ["h"], "State": "exited", "Status": "Exited", "Ports": null,'
            f' "Labels": {{"{podman.LABEL_HOST_PORTS}": "2222:22/tcp,5901:5901/tcp"}}}},'
            ' {"Names": ["p"], "State": "running", "Status": "Up", "Labels": {},'
            ' "Ports": [{"host_port": 2223, "container_port": 22, "protocol": "tcp"}]}]')
    monkeypatch.setattr(podman, "_output", lambda cmd, check=True: rows)
    h, p = podman.list_containers()
    assert h.ports == [podman.PortMap(2222, 22, "tcp"), podman.PortMap(5901, 5901, "tcp")]
    assert p.ports == [podman.PortMap(2223, 22, "tcp")]


def test_auto_ports_skip_a_stopped_host_network_container(make, monkeypatch):
    other = podman.ContainerInfo("h", "exited", "Exited", {}, [podman.PortMap(2222, 22, "tcp"),
                                                               podman.PortMap(5901, 5901, "tcp")])
    monkeypatch.setattr(podman, "list_containers", lambda name=None: [other])
    monkeypatch.setattr(podman, "_port_free", lambda addr, port, proto: True)
    assert podman.pick_auto_ports(make(network={"mode": "host"}, ports={"ssh": 0, "vnc": 0}), None) == {"ssh": 2223, "vnc": 5902}


def test_host_display_in_use(make, monkeypatch, tmp_path):
    cfg = make(network={"mode": "host"})
    unix = tmp_path / "unix"
    unix.write_text("Num RefCount\n0000 2 0 1 1 123 @/tmp/.X11-unix/X1\n")
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: real_read(unix, *a, **k)
                        if str(self) == "/proc/net/unix" else real_read(self, *a, **k))
    with pytest.raises(CasperError, match="display :1"):
        podman.check_host_display(cfg, None)
    podman.check_host_display(cfg, podman.ContainerInfo("x", "running", "Up", {}, []))  # being replaced
    podman.check_host_display(make(), None)  # pasta: not shared


# ---- container.rdma ----

@pytest.mark.parametrize(("raw", "names"), [
    ("", []), ("mlx5_0", ["mlx5_0"]), ("mlx5_0, mlx5_1", ["mlx5_0", "mlx5_1"]), ("all", ["all"]),
])
def test_rdma_names(make, raw, names):
    assert make(container={"rdma": raw}).rdma == names


@pytest.mark.parametrize("raw", ["all,mlx5_0", "mlx5_0;x", "-a"])
def test_invalid_rdma(make, raw):
    with pytest.raises(CasperError, match="container.rdma"):
        make(container={"rdma": raw})


def fake_rdma(tmp_path: Path, devices: dict[str, list[str]]):
    """A fake /sys/class/infiniband and /dev/infiniband: device -> its uverbs / umad nodes."""
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    dev_root.mkdir()
    for dev, nodes in devices.items():
        for node in nodes:
            kind = "infiniband_verbs" if node.startswith("uverbs") else "infiniband_mad"
            (sys_root / dev / "device" / kind / node).mkdir(parents=True)
            (dev_root / node).write_text("")
    (dev_root / "rdma_cm").write_text("")
    return sys_root, dev_root


def test_rdma_devices(tmp_path):
    sys_root, dev_root = fake_rdma(tmp_path, {"mlx5_0": ["uverbs0", "umad0"], "mlx5_1": ["uverbs1"]})
    assert podman.rdma_devices(["mlx5_1"], sys_root, dev_root) == [str(dev_root / "uverbs1"), str(dev_root / "rdma_cm")]
    both = podman.rdma_devices(["all"], sys_root, dev_root)
    assert [Path(p).name for p in both] == ["uverbs0", "umad0", "uverbs1", "rdma_cm"]
    assert podman.rdma_devices([], sys_root, dev_root) == []
    with pytest.raises(CasperError, match=r"not found: mlx5_9 \(available: mlx5_0, mlx5_1\)"):
        podman.rdma_devices(["mlx5_9"], sys_root, dev_root)
    with pytest.raises(CasperError, match="no RDMA devices"):
        podman.rdma_devices(["all"], tmp_path / "none", dev_root)


def test_rdma_device_without_uverbs(tmp_path):
    sys_root, dev_root = fake_rdma(tmp_path, {"mlx5_0": ["umad0"]})
    with pytest.raises(CasperError, match="no uverbs node"):
        podman.rdma_devices(["mlx5_0"], sys_root, dev_root)


def test_rdma_run_args(make):
    cfg = make(container={"rdma": "all"})
    cfg.rdma_paths = ["/dev/infiniband/uverbs0", "/dev/infiniband/rdma_cm"]
    r = podman.run_args(cfg)
    assert [r[i + 1] for i, a in enumerate(r) if a == "--device"] == cfg.rdma_paths
    assert "IPC_LOCK" in r and "memlock=-1:-1" in r
    plain = podman.run_args(make())
    assert "IPC_LOCK" not in plain and "memlock=-1:-1" not in plain


def test_memlock_check(monkeypatch):
    import resource

    monkeypatch.setattr(podman.resource, "getrlimit", lambda r: (65536, 65536))
    with pytest.raises(CasperError, match="limits.d"):
        podman.check_memlock()
    monkeypatch.setattr(podman.resource, "getrlimit", lambda r: (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    podman.check_memlock()


def test_template_defaults():
    doc = tomllib.loads(render_dev_template(DEV_DEFAULT_OS))
    assert doc["network"] == {"mode": "pasta"}
    assert (doc["container"]["gpus"], doc["container"]["rdma"]) == ("", "")
    assert doc["ports"]["bind_address"] == "0.0.0.0"


def test_casperbox_rejects_build_packages(tmp_path):
    vivado = tmp_path / "Xilinx"
    vivado.mkdir()
    doc = {"version": "m2021a", "vivado": {"dir": str(vivado)}, "paths": {"license_dir": str(tmp_path)},
           "build": {"packages": ["git"]}}
    with pytest.raises(CasperError, match=r"Unknown key\(s\) in build: packages"):
        parse_config(doc, tmp_path / "casperbox.toml")
