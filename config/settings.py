"""
全局配置
=========

系统全局配置，支持从环境变量、配置文件加载。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StorageConfig:
    """存储配置"""

    backend: str = "sqlite"  # sqlite | postgresql | mysql
    database_url: str = "sqlite:///maia.db"
    file_store_path: str = "./data/files"
    cache_backend: str = "memory"  # memory | redis
    redis_url: Optional[str] = None


@dataclass
class SchedulerConfig:
    """调度器配置"""

    default_device: str = "cpu"  # cpu | cuda | npu
    max_concurrent_tasks: int = 10
    gpu_memory_fraction: float = 0.9
    load_balance_interval: int = 30  # seconds


@dataclass
class AgentConfig:
    """Agent 配置"""

    max_agents: int = 100
    agent_timeout: int = 300  # seconds
    heartbeat_interval: int = 10
    communication_mode: str = "pubsub"  # pubsub | rpc


@dataclass
class SecurityConfig:
    """安全配置"""

    sandbox_enabled: bool = True
    sandbox_type: str = "docker"  # docker | process | wasm
    code_verification_required: bool = True
    audit_log_enabled: bool = True
    max_code_size: int = 1024 * 1024  # 1MB


@dataclass
class ObservabilityConfig:
    """可观测性配置"""

    metrics_enabled: bool = True
    metrics_port: int = 9090
    tracing_enabled: bool = True
    tracing_backend: str = "otel"  # otel | jaeger | zipkin
    alert_webhook_url: Optional[str] = None


@dataclass
class AppConfig:
    """应用配置"""

    app_name: str = "MAIA Engine"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list = field(default_factory=lambda: ["*"])


@dataclass
class Settings:
    """全局配置"""

    app: AppConfig = field(default_factory=AppConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量加载配置。"""
        # TODO: 实现环境变量加载逻辑
        return cls()


# 全局单例
settings = Settings()
