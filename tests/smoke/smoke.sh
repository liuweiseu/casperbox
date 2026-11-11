#!/usr/bin/env bash
# Smoke test: create a container with casperbox or casperbox-dev and check that the
# environment comes up: SSH (password login), VNC, an extra forwarded port, mounts,
# the MAC address and the basic software. Toolflow functionality is not tested here.
#
# Usage:
#   tests/smoke/smoke.sh casperbox
#   tests/smoke/smoke.sh casperbox-dev [--os IMAGE]
#
# Requires: the casperbox tools on PATH, rootless podman + pasta, sshpass, curl.
# Host ports can be changed with SMOKE_SSH_PORT / SMOKE_VNC_PORT / SMOKE_HTTP_PORT;
# SMOKE_APT_MIRROR sets [build] apt_mirror (and checks that the image uses it).
# The test container is called smoke-<tool> and is removed at the end.
# SC2016: commands in single quotes are expanded inside the container, not here.
# SC2088: "~/..." only appears in messages.
# shellcheck disable=SC2016,SC2088
set -euo pipefail

tool="${1:-}"
shift || true
os_image="ubuntu:26.04"
while (( $# )); do
    case "$1" in
        --os) os_image="${2:?--os needs a value}"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "${tool}" in
    casperbox|casperbox-dev) ;;
    *) echo "Usage: $0 casperbox | casperbox-dev [--os IMAGE]" >&2; exit 2 ;;
esac
for cmd in "${tool}" podman sshpass curl; do
    command -v "${cmd}" >/dev/null || { echo "Missing command: ${cmd}" >&2; exit 2; }
done

ssh_port="${SMOKE_SSH_PORT:-22022}"
vnc_port="${SMOKE_VNC_PORT:-25901}"
http_port="${SMOKE_HTTP_PORT:-28888}"
apt_mirror="${SMOKE_APT_MIRROR:-}"
name="smoke-${tool}"
password="casper"
mac="02:11:22:33:44:55"

failures=0
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }
step() { echo; echo "==> $*"; }

# Physical path: Vivado is mounted at its resolved path inside the container
work="$(cd "$(mktemp -d)" && pwd -P)"
cleanup() {
    local rc=$?
    if (( rc != 0 || failures != 0 )); then
        step "Container logs (for debugging)"
        podman logs "${name}" 2>&1 | tail -n 50 || true
    fi
    step "Removing the test container"
    (cd "${work}" && "${tool}" down) || true
    rm -rf "${work}"
    exit "${rc}"
}
trap cleanup EXIT
cd "${work}"

# ---------------------------------------------------------------------------
step "Creating the config with ${tool} init"
# ---------------------------------------------------------------------------
mkdir data
echo "hello from the host" > data/hello.txt

if [[ "${tool}" == casperbox ]]; then
    config=casperbox.toml
    # Stand-in for a Vivado install: only settings64.sh is needed to start the container
    mkdir -p xilinx/Vivado/2021.1 lic
    echo "# stub settings64.sh for the smoke test" > xilinx/Vivado/2021.1/settings64.sh
    "${tool}" init
    sed -i -e 's|^dir = "/tools/Xilinx"|dir = "./xilinx"|' \
           -e 's|^license_dir = "~/.Xilinx"|license_dir = "./lic"|' "${config}"
else
    config=casper-dev.toml
    "${tool}" init --os "${os_image}"
    # python3 is used below for the forwarded-port test; it also tests `packages`
    sed -i 's|^packages = \[|packages = ["python3",|' "${config}"
fi
sed -i -e "s|^name = \".*\"|name = \"${name}\"|" \
       -e "s|^mac = \"\"|mac = \"${mac}\"|" \
       -e "s|^ssh = [0-9]*|ssh = ${ssh_port}|" \
       -e "s|^vnc = [0-9]*|vnc = ${vnc_port}|" \
       -e "s|^apt_mirror = \"\"|apt_mirror = \"${apt_mirror}\"|" "${config}"
cat >> "${config}" <<EOF

[[mounts]]
host = "./data"
container = "~/data"
mode = "ro"

[[extra_ports]]
host = ${http_port}
container = 8888
EOF

"${tool}" config

# ---------------------------------------------------------------------------
step "Starting the container with ${tool} up"
# ---------------------------------------------------------------------------
"${tool}" up --yes

ssh_run() {
    sshpass -p "${password}" ssh -p "${ssh_port}" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
        -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=5 \
        casper@127.0.0.1 "$@"
}
# Run a command in a login shell, where the toolflow environment is loaded
ssh_login() { ssh_run "bash -lc $(printf '%q' "$1")"; }

step "Waiting for SSH"
for _ in $(seq 60); do
    ssh_run true 2>/dev/null && break
    sleep 2
done

# ---------------------------------------------------------------------------
step "Checking the environment"
# ---------------------------------------------------------------------------
check() {  # check DESCRIPTION EXPECTED ACTUAL
    if [[ "$3" == "$2" ]]; then pass "$1"; else fail "$1: expected '$2', got '$3'"; fi
}

check "SSH password login (port ${ssh_port})" casper "$(ssh_run whoami 2>&1 || true)"

banner="$(timeout 10 bash -c "exec 3<>/dev/tcp/127.0.0.1/${vnc_port}; head -c 3 <&3" 2>/dev/null || true)"
check "VNC server answers (port ${vnc_port})" RFB "${banner}"

# The XFCE desktop came up (a broken session leaves a black screen with VNC still answering)
panel=no
for _ in $(seq 30); do
    panel="$(ssh_run 'pgrep -x xfce4-panel >/dev/null && echo yes || echo no' 2>/dev/null || true)"
    [[ "${panel}" == yes ]] && break
    sleep 2
done
check "XFCE desktop running (xfce4-panel)" yes "${panel}"
# Look at the executables: `pgrep -f` would also match the shell running this command
check "Desktop session bus is the system dbus" no \
    "$(ssh_run 'for p in $(pgrep -x dbus-daemon); do readlink "/proc/$p/exe"; done | grep -q /opt/conda && echo yes || echo no')"

check "MAC address of eth0" "${mac}" "$(ssh_run cat /sys/class/net/eth0/address 2>&1 || true)"
check "Host UID/GID inside the container (keep-id)" "$(id -u):$(id -g)" "$(ssh_run 'echo $(id -u):$(id -g)' 2>&1 || true)"
check "Read-only mount ~/data" "hello from the host" "$(ssh_run cat data/hello.txt 2>&1 || true)"
check "Mount is read-only" denied "$(ssh_run 'touch data/x 2>/dev/null && echo written || echo denied')"
check "sudo with the container password" root "$(ssh_run "echo ${password} | sudo -S -p '' whoami" 2>&1 || true)"
if [[ -n "${apt_mirror}" ]]; then
    check "apt sources use build.apt_mirror" yes \
        "$(ssh_run "grep -rqsF '${apt_mirror%/}/' /etc/apt/sources.list /etc/apt/sources.list.d && echo yes || echo no")"
fi
check "Firefox installed" yes "$(ssh_login 'command -v firefox >/dev/null && echo yes || echo no')"

# Extra forwarded port: serve a file from the container and fetch it from the host
podman exec -d -u casper -w /home/casper/data "${name}" bash -lc 'exec python3 -m http.server 8888' >/dev/null
body=""
for _ in $(seq 15); do
    body="$(curl -fsS "http://127.0.0.1:${http_port}/hello.txt" 2>/dev/null || true)"
    [[ -n "${body}" ]] && break
    sleep 1
done
check "Extra port ${http_port} -> 8888" "hello from the host" "${body}"

if [[ "${tool}" == casperbox ]]; then
    check "conda env active in login shells" casper "$(ssh_login 'echo ${CONDA_DEFAULT_ENV:-}')"
    check "XILINX_PATH points to the Vivado mount" "${work}/xilinx/Vivado/2021.1" "$(ssh_login 'echo ${XILINX_PATH:-}')"
    check "mlib_devel cloned" yes "$(ssh_run 'test -f toolflow/mlib_devel/startsg && echo yes || echo no')"
    check "~/.startsg.local generated" yes "$(ssh_run 'test -f .startsg.local && echo yes || echo no')"
    check "~/.bashrc sources ~/.startsg.local" yes \
        "$(ssh_run 'grep -qF "source ~/.startsg.local" .bashrc && echo yes || echo no')"
    check "startsg linked in ~/.local/bin" "/home/casper/toolflow/mlib_devel/startsg" \
        "$(ssh_run 'readlink .local/bin/startsg')"
    check "JASPER_BACKEND set from ~/.startsg.local" vivado "$(ssh_login 'echo ${JASPER_BACKEND:-}')"
    check "MATLAB_PATH set to /tmp" /tmp "$(ssh_login 'echo ${MATLAB_PATH:-}')"
    check "FRONTEND set to scilab" scilab "$(ssh_login 'echo ${FRONTEND:-}')"
    check "CASPER_DEBUG set to off" off "$(ssh_login 'echo ${CASPER_DEBUG:-}')"
    check "pyqt6, pyqtgraph, rich importable" yes \
        "$(ssh_login 'QT_QPA_PLATFORM=offscreen python -c "import PyQt6.QtWidgets, pyqtgraph, rich" && echo yes || echo no')"
    check "device-tree-xlnx cloned" yes "$(ssh_run 'test -d toolflow/device-tree-xlnx && echo yes || echo no')"
    check "scilab on PATH" "/home/casper/.local/bin/scilab" "$(ssh_login 'command -v scilab')"
    # versions/m2021a/pkgs.list (Vivado runtime dependencies) was installed
    check "version packages (pkgs.list) installed" install \
        "$(ssh_run "dpkg-query -W -f='\${Status}' libtinfo5 2>/dev/null | cut -d' ' -f1")"
    check "installLibs.sh packages (pkgs.list) installed" install \
        "$(ssh_run "dpkg-query -W -f='\${Status}' libc6-dev-i386 2>/dev/null | cut -d' ' -f1")"
    # versions/m2021a/patch/50-libudev.sh: libudev preloaded for Vivado 2021.1 in interactive shells
    check "patch/50-libudev.sh: LD_PRELOAD in ~/.bashrc" yes \
        "$(ssh_run 'grep -qF "LD_PRELOAD=\"/lib/x86_64-linux-gnu/libudev.so.1\"" .bashrc && echo yes || echo no')"
    ssh_run 'touch work/from-container' || true
    check "~/work is writable and owned by the host user" "$(id -u)" "$(stat -c %u work/from-container 2>/dev/null || echo missing)"
fi

# ---------------------------------------------------------------------------
step "Restarting the container with ${tool} restart (data must be kept)"
# ---------------------------------------------------------------------------
ssh_run 'echo kept > restart-marker' || true
"${tool}" restart
for _ in $(seq 60); do
    ssh_run true 2>/dev/null && break
    sleep 2
done
check "File outside the mounts kept after restart" kept "$(ssh_run 'cat restart-marker' 2>&1 || true)"
banner=""
for _ in $(seq 15); do
    banner="$(timeout 10 bash -c "exec 3<>/dev/tcp/127.0.0.1/${vnc_port}; head -c 3 <&3" 2>/dev/null || true)"
    [[ "${banner}" == RFB ]] && break
    sleep 2
done
check "VNC server answers after restart" RFB "${banner}"

# ---------------------------------------------------------------------------
step "Saving the container as an image with ${tool} commit"
# ---------------------------------------------------------------------------
snapshot="localhost/${name}:smoke-snapshot"
commit_args=("${snapshot}")
# The toolflow image is large; only save the dev image to a tar file
[[ "${tool}" == casperbox-dev ]] && commit_args+=(-o "${work}/snapshot.tar")
"${tool}" commit "${commit_args[@]}"
check "Snapshot keeps the changes made in the container" kept \
    "$(podman run --rm --entrypoint cat "${snapshot}" /home/casper/restart-marker 2>&1 || true)"
check "Snapshot has no casperbox build hash" "" \
    "$(podman image inspect -f '{{index .Labels "casperbox.build-hash"}}' "${snapshot}" 2>&1 || true)"
if [[ "${tool}" == casperbox-dev ]]; then
    check "Snapshot saved to a tar file" yes "$( [[ -s "${work}/snapshot.tar" ]] && echo yes || echo no)"
fi
podman rmi -f "${snapshot}" >/dev/null || true

# ---------------------------------------------------------------------------
step "Status and ps"
# ---------------------------------------------------------------------------
"${tool}" status
"${tool}" ps

echo
if (( failures )); then
    echo "Smoke test FAILED: ${failures} check(s) failed"
    exit 1
fi
if [[ "${tool}" == casperbox-dev ]]; then
    echo "Smoke test passed (${tool}, ${os_image})"
else
    echo "Smoke test passed (${tool})"
fi
