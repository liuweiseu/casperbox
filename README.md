# casper-container

用 rootless podman + podman compose 构建并运行 CASPER toolflow 开发容器。

镜像包含：openssh-server、TigerVNC（XFCE 桌面）、Firefox、Miniconda（conda 环境）、
mlib_devel、device-tree-xlnx、Scilab。Vivado 和 license 从宿主机挂载进来。

## 支持的版本

| 版本 | 基础镜像 | Vivado | Python | mlib_devel | device-tree-xlnx | Scilab |
|---|---|---|---|---|---|---|
| m2021a | Ubuntu 20.04 | 2021.1 | 3.14 | [liuweiseu/mlib_devel](https://github.com/liuweiseu/mlib_devel) `scilab-dev-ai` | xlnx_rel_v2021.1 | 2024.1.0 |

## 宿主机要求

- rootless podman >= 4.4（以普通用户运行）及 passt 软件包（提供 pasta）
- podman-compose >= 1.1.0（`podman compose` 会被强制使用 podman-compose，docker-compose 不支持 `userns keep-id`）
- 已安装的 Vivado（版本见上表）和 Xilinx license

## 宿主机环境配置

通过 SSH 或桌面直接登录运行 podman 的用户（不要用 `su` 切换），然后执行：

```bash
loginctl enable-linger                      # 保持用户会话，注销后容器不停止
grep "^$(id -un):" /etc/subuid /etc/subgid  # 应各有一行，否则需管理员分配
podman system migrate
podman info --format '{{.Host.CgroupManager}}'   # 输出 systemd 且无 WARN 即正常
```

## 快速开始

```bash
cp config/user.conf.example config/user.conf
vi config/user.conf            # 填写 VIVADO_DIR / XIL_LICENSE_DIR / WORK_DIR / 端口

./casper.sh build              # 构建镜像（只有一个版本时自动选择）
./casper.sh up                 # 启动容器
```

连接：

- SSH：`ssh -p <SSH_PORT> casper@<宿主机>`，密码为 `CONTAINER_PWD`（默认 `casper`）
- VNC：`<宿主机>:<VNC_PORT>`，密码为 `VNC_PASSWORD`（默认 `casper`）
- 本机：`./casper.sh shell`

## 命令

| 命令 | 说明 |
|---|---|
| `./casper.sh list` | 列出支持的版本 |
| `./casper.sh build [版本] [--no-cache]` | 构建镜像 |
| `./casper.sh up [版本]` | 启动（重建）容器，镜像不存在时自动构建 |
| `./casper.sh down` | 停止并删除容器 |
| `./casper.sh restart` | 重启容器 |
| `./casper.sh shell` | 以容器用户身份进入容器 |
| `./casper.sh status` / `logs` | 状态 / 日志 |
| `./casper.sh config` | 查看合并后的 compose 配置（密码已隐藏） |
| `./casper.sh -c other.conf up` | 使用其它配置文件 |

## 目录与挂载

| 宿主机（user.conf） | 容器 |
|---|---|
| `VIVADO_DIR` | `/tools/Xilinx`（默认只读） |
| `XIL_LICENSE_DIR` | `~/.Xilinx` |
| `WORK_DIR` | `~/work` |
| `EXTRA_MOUNTS`（可选） | 自定义 |

容器内的 toolflow 位于 `~/toolflow`：

```
~/toolflow/
├── mlib_devel/            # 含自动生成的 startsg.local
├── device-tree-xlnx/
└── src/                   # requirements.txt 中以 -e 安装的依赖
```

登录 shell 会自动加载 Vivado `settings64.sh`、`startsg.local` 中的变量，并激活 conda 环境 `casper`。

Scilab 解压在 `/opt/scilab`，可执行文件链接为 `~/.local/bin/scilab`（`~/.local/bin` 已加入 PATH），
直接运行 `scilab` 即可。

> 注意：`~/toolflow` 存在于镜像中，不是挂载目录。`down`/`up` 会重建容器，对其所做的修改会丢失，
> 请把工程放在 `~/work` 下。

## 网络（pasta）

容器使用 rootless podman 的 pasta 网络模式（`network_mode: pasta:...`），不创建 bridge 网络：

- SSH / VNC / `EXTRA_PORTS` 端口仍通过 `-p` 转发，由 podman 交给 pasta 处理；
- 容器内网卡固定命名为 `eth0`（pasta 默认会沿用宿主机网卡名）；
- 容器内看到的 IP 与宿主机相同。

### 固定 MAC 地址

在 `user.conf` 中设置 `CONTAINER_MAC=xx:xx:xx:xx:xx:xx`，脚本会通过 pasta 的 `--ns-mac-addr`
把它设置到容器的 `eth0` 上，可用于绑定 MAC 的 Xilinx node-locked license。留空则随机分配。

> pasta 的网卡只存在于容器自己的网络命名空间中，因此使用与宿主机物理网卡相同的 MAC 不会与局域网冲突。

## 修改配置后需要做什么

| 修改项 | 操作 |
|---|---|
| 目录、端口、密码、`CONTAINER_MAC`、`EXTRA_*` | `./casper.sh up` |
| `CONTAINER_USER`，或换了宿主机用户（UID/GID 变化） | `./casper.sh build` 后 `./casper.sh up` |

## 仓库结构

```
casper.sh              入口脚本
compose.yml            基础 compose 定义（变量来自 .generated/.env）
config/                用户配置
versions/<ver>/        各版本 Dockerfile 与 toolflow.conf（受 SHA256SUMS 保护，用户不要修改）
common/scripts/        镜像构建脚本
common/rootfs/         拷贝进镜像的文件（sshd 配置、profile、entrypoint）
common/vnc/xstartup    VNC 会话脚本
lib/                   casper.sh 的函数库
tools/                 维护工具
.generated/            运行时生成的 .env 与 compose.override.yml
```

## 维护者：添加新版本

1. 复制 `versions/m2021a` 为 `versions/<新版本>`，修改 `toolflow.conf`（`TOOLFLOW_VERSION` 必须与目录名一致），按需调整 Dockerfile。
2. 执行 `tools/update-checksums.sh` 更新 `versions/SHA256SUMS`。
