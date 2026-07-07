"""
全局配置
=========

系统全局配置，支持从环境变量、.env 文件加载。

配置优先级: 环境变量 > .env 文件 > 代码默认值
"""

from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Optional

# 自动加载项目根目录的 .env 文件
def _load_dotenv():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

_load_dotenv()


@dataclass
class LLMConfig:
    """LLM 配置 — 默认 DeepSeek v4

    api_key 读取优先级:
    1. 环境变量 DEEPSEEK_API_KEY
    2. 项目根目录 .env 文件中的 DEEPSEEK_API_KEY=xxx
    3. 代码中直接赋值 settings.llm.api_key = "xxx"
    """

    provider: str = "deepseek"
    api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-pro"
    max_tokens: int = 100000
    temperature: float = 0.7
    streaming: bool = True
    timeout: int = 60


@dataclass
class DoubaoConfig:
    """豆包 (Doubao) LLM 配置"""

    api_key: str = field(default_factory=lambda: os.getenv("DOUBAO_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    model: str = field(default_factory=lambda: os.getenv("DOUBAO_MODEL", "doubao-pro-32k"))


def get_llm_api_config(provider: str = "deepseek") -> dict:
    """API 调用：返回 LLM 配置（含 api_key）"""
    if provider == "doubao":
        return {
            "provider": "doubao",
            "api_key": settings.doubao.api_key,
            "base_url": settings.doubao.base_url,
            "model": settings.doubao.model,
        }
    return {
        "provider": "deepseek",
        "api_key": settings.llm.api_key,
        "base_url": settings.llm.base_url,
        "model": settings.llm.model,
    }


def get_llm_config(provider: str = "deepseek"):
    """根据 provider 返回对应的 LLM 配置"""
    if provider == "doubao":
        cfg = DoubaoConfig()
        return LLMConfig(
            provider="doubao",
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
        )
    return settings.llm


@dataclass
class StorageConfig:
    """存储配置"""

    backend: str = "sqlite"  # sqlite | postgresql | mysql
    database_url: str = "sqlite:///sotaband.db"
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

    app_name: str = "SOTABand"
    debug: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = 8001
    cors_origins: list = field(default_factory=lambda: ["*"])


@dataclass
class Settings:
    """全局配置"""

    app: AppConfig = field(default_factory=AppConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    doubao: DoubaoConfig = field(default_factory=DoubaoConfig)
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
