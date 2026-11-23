from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./var/ase.db"
    checkpoint_backend: str = "auto"
    db_pool_timeout: int = 10
    db_connect_timeout: int = 5
    api_port: int = 8000
    api_key: str = "dev-approval-key"
    workspace_root: Path = Path("./var/workspaces")
    sandbox_image: str = "python:3.12-slim"
    sandbox_memory: str = "512m"
    agent_server_memory: str = "2g"
    agent_readonly_root: bool = False
    sandbox_cpus: str = "1.0"
    sandbox_pids_limit: int = 128
    sandbox_timeout_seconds: int = 120
    sandbox_network: str = "none"
    agent_server_network: str = "bridge"
    coding_runtime: str = "scripted"
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-flash"
    llm_api_base: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    llm_timeout: int = 180
    openhands_max_iterations: int = 40
    openhands_host: str = ""
    agent_server_image: str = "ghcr.io/openhands/agent-server:1.63.1-python"
    max_repair_attempts: int = 2
    agent_timeout_seconds: int = 600
    repository_lease_seconds: int = 1800

    ase_source_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
