# ADR-010 容器加固

状态：已接受。

默认容器保持 Phase 1.1 已经 inspect 过的限制，根文件系统可写。

只读根文件系统作为显式配置。PyInstaller 入口必须在可执行的 `/tmp` tmpfs 上解压，否则报 `libz.so.1: failed to map segment`。`/tmp` 不能设成 `noexec`。pytest 放进自建镜像，避免只读根上再安装包。

验证用的镜像是 `ase-agent-server:1.63.1`，用户是 `openhands`。
