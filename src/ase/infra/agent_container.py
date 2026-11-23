import hashlib
import json
import os
import secrets
import threading
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ase.config import Settings
from ase.infra.policy import assert_workspace_allowed


@dataclass
class ContainerHandle:
    container_id: str
    name: str
    host: str
    session_api_key: str
    workspace_path: str
    image: str
    limits: dict[str, Any] = field(default_factory=dict)


def workspace_label(workspace: Path) -> str:
    return hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()[:32]


class AgentContainerManager:
    """Starts one hardened agent-server container per worktree."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._by_id: dict[str, ContainerHandle] = {}
        self._by_workspace: dict[str, ContainerHandle] = {}
        self._start_lock = threading.Lock()

    def start(self, workspace: Path) -> ContainerHandle:
        with self._start_lock:
            return self._start_locked(workspace)

    def _start_locked(self, workspace: Path) -> ContainerHandle:
        resolved = assert_workspace_allowed(workspace, self.settings)
        existing = self._by_workspace.get(str(resolved))
        if existing is not None:
            return existing
        adopted = self._adopt(resolved)
        if adopted is not None:
            return adopted
        self._require_docker()
        self._require_image()
        _bypass_localhost_proxy()
        port = _free_port()
        name = f"ase-agent-{port}"
        session_key = secrets.token_urlsafe(24)
        args = self.build_run_args(resolved, port, name, session_key)
        completed = _run(args, timeout=120)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().replace(session_key, "[redacted]")
            raise RuntimeError(f"agent container failed to start: {detail[-2000:]}")
        container_id = (completed.stdout or "").strip()
        host = f"http://127.0.0.1:{port}"
        try:
            self._wait_healthy(host)
            limits = self.inspect_limits(container_id, resolved)
            self._ensure_pytest(container_id)
        except Exception:
            self.stop(container_id)
            raise
        handle = ContainerHandle(
            container_id=container_id,
            name=name,
            host=host,
            session_api_key=session_key,
            workspace_path=str(resolved),
            image=self.settings.agent_server_image,
            limits=limits,
        )
        self._by_id[container_id] = handle
        self._by_workspace[str(resolved)] = handle
        return handle

    def require_workspace(self, workspace: Path) -> ContainerHandle:
        resolved = assert_workspace_allowed(workspace, self.settings)
        handle = self._by_workspace.get(str(resolved))
        if handle is None:
            raise RuntimeError(f"no agent container for {resolved}")
        return handle

    def build_run_args(self, workspace: Path, port: int, name: str, session_key: str) -> list[str]:
        resolved = assert_workspace_allowed(workspace, self.settings)
        args = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--label",
            "ase.role=agent",
            "--label",
            f"ase.workspace={workspace_label(resolved)}",
            f"--memory={self.settings.agent_server_memory}",
            f"--cpus={self.settings.sandbox_cpus}",
            f"--pids-limit={self.settings.sandbox_pids_limit}",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--network",
            self.settings.agent_server_network,
            "--ulimit",
            "nofile=65536:65536",
            "-p",
            f"127.0.0.1:{port}:8000",
            "-e",
            "OH_ENABLE_VSCODE=false",
            "-e",
            f"OH_SESSION_API_KEYS_0={session_key}",
            "-v",
            f"{resolved}:/workspace:rw",
            "-w",
            "/workspace",
        ]
        if self.settings.agent_readonly_root:
            args.extend(
                [
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,exec,nosuid,size=1610612736",
                    "--tmpfs",
                    "/var/tmp:rw,exec,nosuid,size=134217728",
                    "--tmpfs",
                    "/run:rw,nosuid,size=16777216",
                    "--tmpfs",
                    "/home/openhands:rw,exec,nosuid,size=268435456",
                ]
            )
        args.extend(
            [
            self.settings.agent_server_image,
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            ]
        )
        rendered = " ".join(args)
        if "--privileged" in args or "docker.sock" in rendered:
            raise PermissionError("agent container command is not allowed")
        return args

    def inspect_limits(self, container_id: str, workspace: Path) -> dict[str, Any]:
        completed = _run(["docker", "inspect", container_id], timeout=30)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "docker inspect failed")
        payload = json.loads(completed.stdout)[0]
        return assert_container_limits(payload, workspace)

    def execute(self, container_id: str, command: list[str], timeout: int) -> tuple[int, str, float]:
        started = time.perf_counter()
        try:
            completed = _run(["docker", "exec", container_id, *command], timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "command timed out", round(time.perf_counter() - started, 3)
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, output[-8000:], round(time.perf_counter() - started, 3)

    def _adopt(self, workspace: Path) -> ContainerHandle | None:
        self._require_docker()
        found = _run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=ase.role=agent",
                "--filter",
                f"label=ase.workspace={workspace_label(workspace)}",
            ],
            timeout=20,
        )
        container_id = (found.stdout or "").strip().splitlines()
        if found.returncode != 0 or not container_id:
            return None
        handle = self._handle_from_running(container_id[0], workspace)
        self._by_id[handle.container_id] = handle
        self._by_workspace[str(workspace)] = handle
        return handle

    def _handle_from_running(self, container_id: str, workspace: Path) -> ContainerHandle:
        completed = _run(["docker", "inspect", container_id], timeout=30)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "docker inspect failed")
        payload = json.loads(completed.stdout)[0]
        env = payload.get("Config", {}).get("Env") or []
        session_key = ""
        for item in env:
            if item.startswith("OH_SESSION_API_KEYS_0="):
                session_key = item.split("=", 1)[1]
        ports = (payload.get("NetworkSettings", {}).get("Ports") or {}).get("8000/tcp") or []
        host_port = ports[0]["HostPort"] if ports else ""
        if not host_port or not session_key:
            raise RuntimeError("running agent container is missing its published port or session key")
        limits = assert_container_limits(payload, workspace)
        return ContainerHandle(
            container_id=payload.get("Id") or container_id,
            name=(payload.get("Name") or "").lstrip("/"),
            host=f"http://127.0.0.1:{host_port}",
            session_api_key=session_key,
            workspace_path=str(workspace),
            image=payload.get("Config", {}).get("Image") or self.settings.agent_server_image,
            limits=limits,
        )

    def stop(self, container_id: str) -> None:
        _run(["docker", "rm", "-f", container_id], timeout=30)
        handle = self._by_id.pop(container_id, None)
        if handle is not None:
            self._by_workspace.pop(handle.workspace_path, None)

    def stop_all(self) -> None:
        for container_id in list(self._by_id):
            self.stop(container_id)

    def _require_docker(self) -> None:
        if _run(["docker", "info"], timeout=20).returncode != 0:
            raise RuntimeError("Docker daemon is not available")

    def _require_image(self) -> None:
        image = self.settings.agent_server_image
        if _run(["docker", "image", "inspect", image], timeout=20).returncode == 0:
            return
        raise RuntimeError(
            f"agent server image {image} is missing. "
            "Pull it with `docker pull ghcr.io/openhands/agent-server:1.63.1-python` "
            "or build docker/agent-server.Dockerfile."
        )

    def _wait_healthy(self, host: str) -> None:
        deadline = time.perf_counter() + 90
        last = ""
        while time.perf_counter() < deadline:
            try:
                with urllib.request.urlopen(host + "/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except Exception as exc:
                last = type(exc).__name__
            time.sleep(1)
        raise RuntimeError(f"agent container health check failed: {last}")

    def _ensure_pytest(self, container_id: str) -> None:
        code, _output, _elapsed = self.execute(container_id, ["python", "-m", "pytest", "--version"], timeout=30)
        if code == 0:
            return
        code, output, _elapsed = self.execute(
            container_id,
            ["python", "-m", "pip", "install", "pytest", "-q"],
            timeout=180,
        )
        if code != 0:
            raise RuntimeError(f"pytest bootstrap inside the agent container failed: {output[-1000:]}")


def assert_container_limits(payload: dict[str, Any], workspace: Path) -> dict[str, Any]:
    host = payload["HostConfig"]
    if host.get("Privileged"):
        raise PermissionError("container is privileged")
    if int(host.get("Memory") or 0) <= 0:
        raise PermissionError("container has no memory limit")
    if int(host.get("NanoCpus") or 0) <= 0:
        raise PermissionError("container has no cpu limit")
    if int(host.get("PidsLimit") or 0) <= 0:
        raise PermissionError("container has no pid limit")
    caps = host.get("CapDrop") or []
    if "ALL" not in caps:
        raise PermissionError("container did not drop all capabilities")
    security = " ".join(host.get("SecurityOpt") or [])
    if "no-new-privileges" not in security:
        raise PermissionError("container did not set no-new-privileges")
    binds = host.get("Binds") or []
    allowed = workspace.resolve()
    for bind in binds:
        if "docker.sock" in bind:
            raise PermissionError("container mounts docker.sock")
        source = _bind_source(bind)
        if Path(source).resolve() != allowed:
            raise PermissionError(f"container mounts an unauthorized path: {source}")
    config = payload.get("Config") or {}
    return {
        "memory": host.get("Memory"),
        "nano_cpus": host.get("NanoCpus"),
        "pids_limit": host.get("PidsLimit"),
        "privileged": host.get("Privileged"),
        "cap_drop": caps,
        "security_opt": host.get("SecurityOpt"),
        "binds": binds,
        "user": config.get("User") or "root",
        "readonly_rootfs": bool(host.get("ReadonlyRootfs")),
        "network_mode": host.get("NetworkMode"),
    }


def _bind_source(bind: str) -> str:
    marker = ":/workspace"
    if marker in bind.replace("\\", "/"):
        normalized = bind.replace("\\", "/")
        return bind[: normalized.index(":/workspace")]
    return bind.split(":", 1)[0]


def _bypass_localhost_proxy() -> None:
    for name in ("NO_PROXY", "no_proxy"):
        parts = [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]
        for item in ("127.0.0.1", "localhost"):
            if item not in parts:
                parts.append(item)
        os.environ[name] = ",".join(parts)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
