# Docker Agent 运行时

编码会话使用一个容器，镜像是 `ghcr.io/openhands/agent-server:1.63.1-python`。这个镜像同时提供 Agent Server 和 Python 执行环境。`python:3.12-slim` 不能代替它。项目依赖不另起容器；计算器 PoC 只使用镜像里的 Python，测试框架由 ASE 在容器内确认。

`AgentContainerManager` 负责生命周期：

1. `docker info` 确认守护进程可用。
2. `docker image inspect` 确认镜像存在。缺失时返回拉取或构建命令，不自动拉取。
3. 只挂载当前任务 worktree 到 `/workspace`。
4. 等待 `GET /health` 返回 200，再用 `docker inspect` 核对资源限制。
5. 容器里没有 pytest 时，由 ASE 执行一次 `python -m pip install pytest`。这是运行时引导，不是代理自己安装依赖。
6. `RemoteWorkspace` 连接 `127.0.0.1` 上的 Agent Server。会话密钥是容器随机令牌，不是模型密钥。
7. pytest 使用 `docker exec <id> python -m pytest -q`。没有容器 ID 时返回退出码 127，不回退到宿主机。
8. 工作流结束或异常时删除容器。

启动参数包含 `--memory`、`--cpus`、`--pids-limit`、`--cap-drop=ALL`、`--security-opt no-new-privileges`、`--network bridge`、`OH_ENABLE_VSCODE=false`，端口只绑定 `127.0.0.1`。镜像用户已经是非 root 的 `openhands`。根文件系统保持可写，因为 Agent Server 需要写自己的运行目录；这一点由 inspect 记录，不伪造成只读。

明确拒绝 `--privileged`、`/var/run/docker.sock` 和授权 worktree 以外的绑定。Windows 绑定路径按 `:/workspace` 分割，避免把 `C:` 当成源目录。

本机 HTTP 代理会把 `127.0.0.1` 请求转走并返回 502。容器管理器在连接 Agent Server 前把 `127.0.0.1` 和 `localhost` 加入 `NO_PROXY`。外部模型请求仍然走系统代理。

可选镜像见 `docker/agent-server.Dockerfile`。它从同一官方标签安装 pytest，避免每次启动都在容器内安装。默认仍使用官方标签。
