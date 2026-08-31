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
        with open(env_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

_load_dotenv()


# OpenAI 兼容协议的主流服务商预设表
# 任何遵循 OpenAI /v1/chat/completions 协议的服务（包括自建网关、第三方聚合平台）
# 都可以通过 <PROVIDER>_API_KEY / <PROVIDER>_BASE_URL / <PROVIDER>_MODEL 环境变量覆盖接入。
PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-pro",
    },
    "openai": {
        "name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "moonshot": {
        "name": "Kimi (Moonshot)",
        "env_key": "MOONSHOT_API_KEY",
        "default_base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "env_key": "ZHIPU_API_KEY",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
    },
    "qwen": {
        "name": "通义千问 (阿里云百炼)",
        "env_key": "DASHSCOPE_API_KEY",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "siliconflow": {
        "name": "硅基流动",
        "env_key": "SILICONFLOW_API_KEY",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
    },
    "minimax": {
        "name": "MiniMax",
        "env_key": "MINIMAX_API_KEY",
        "default_base_url": "https://api.minimaxi.com/v1",
        "default_model": "MiniMax-Text-01",
    },
    "mimo": {
        "name": "MiMo Coding Plan",
        "env_key": "MIMO_API_KEY",
        "default_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        # Token Plan 覆盖 V2.5 系列：MiMo-V2.5 / MiMo-V2.5-Pro
        # 默认取 V2.5；想用 Pro 旗舰请在 .env 设 LLM_MODEL=MiMo-V2.5-Pro
        "default_model": "MiMo-V2.5",
    },
    "doubao": {
        "name": "豆包 (火山方舟)",
        "env_key": "DOUBAO_API_KEY",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-pro-32k",
    },
}


@dataclass
class LLMConfig:
    """LLM 配置 — 默认 DeepSeek v4，支持任意 OpenAI 兼容协议服务商

    推荐写法（统一变量，切换服务商只改两行）:
        LLM_PROVIDER=mimo
        LLM_API_KEY=tp-xxx
        # 可选覆盖（不填则用服务商预设的默认端点/模型）
        # LLM_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
        # LLM_MODEL=MiMo-7B
        # LLM_TIMEOUT=900  # 可选：LLM 请求超时秒数（默认 300；接 1M 长上下文模型建议调大）

    各字段读取优先级（高 → 低）:
        api_key : 代码赋值 > LLM_API_KEY(仅默认provider) > <PROVIDER>_API_KEY > 空
        base_url: 代码赋值 > LLM_BASE_URL(仅默认provider) > <PROVIDER>_BASE_URL > 服务商预设默认值
        model   : 代码赋值 > LLM_MODEL(仅默认provider)    > <PROVIDER>_MODEL    > 服务商预设默认值

    全局变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 跟随 LLM_PROVIDER 指定的
    当前默认 provider；当显式使用其他 provider 时（如测试连接接口传 provider=mimo），
    只读取该服务商的专属变量 <PROVIDER>_API_KEY 等 + 预设默认值，避免误用默认 provider 的 key。
    专属变量（如 DEEPSEEK_API_KEY）保留向后兼容。

    支持的 provider: deepseek / openai / moonshot / zhipu / qwen /
    siliconflow / minimax / mimo / doubao，以及任意自定义名称
    （自定义 provider 同样只需 LLM_PROVIDER + LLM_API_KEY，端点用 LLM_BASE_URL 指定）。
    """

    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "deepseek"))
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    max_tokens: int = 100000
    temperature: float = 0.7
    streaming: bool = True
    # LLM 请求超时（秒），可通过 .env 的 LLM_TIMEOUT 覆盖；
    # 接 1M 长上下文旗舰模型时建议调大（默认 300 = 5 分钟）。
    timeout: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "300")))

    def __post_init__(self):
        """按优先级从环境变量补全 api_key / base_url / model"""
        # provider=None（显式传 None）时回退到环境变量 / 默认值
        self.provider = self.provider or os.getenv("LLM_PROVIDER", "deepseek")
        preset = PROVIDER_PRESETS.get(self.provider)
        prefix = self.provider.upper()
        if preset:
            legacy_key_env = preset["env_key"]
            default_base_url = preset["default_base_url"]
            default_model = preset["default_model"]
        else:
            legacy_key_env = f"{prefix}_API_KEY"
            default_base_url = ""
            default_model = ""
        # 全局 LLM_* 变量只跟随当前默认 provider，避免测试其他服务商时误用
        is_default = self.provider == os.getenv("LLM_PROVIDER", "deepseek")
        global_api_key = os.getenv("LLM_API_KEY", "") if is_default else ""
        global_base_url = os.getenv("LLM_BASE_URL", "") if is_default else ""
        global_model = os.getenv("LLM_MODEL", "") if is_default else ""
        # api_key: 代码赋值 > LLM_API_KEY(默认provider) > <PROVIDER>_API_KEY
        self.api_key = (
            self.api_key
            or global_api_key
            or os.getenv(legacy_key_env, "")
        )
        # base_url: 代码赋值 > LLM_BASE_URL(默认provider) > <PROVIDER>_BASE_URL > 预设默认
        self.base_url = (
            self.base_url
            or global_base_url
            or os.getenv(f"{prefix}_BASE_URL", default_base_url)
        )
        # model: 代码赋值 > LLM_MODEL(默认provider) > <PROVIDER>_MODEL > 预设默认
        self.model = (
            self.model
            or global_model
            or os.getenv(f"{prefix}_MODEL", default_model)
        )


@dataclass
class DoubaoConfig:
    """豆包 (Doubao) LLM 配置"""

    api_key: str = field(default_factory=lambda: os.getenv("DOUBAO_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"))
    model: str = field(default_factory=lambda: os.getenv("DOUBAO_MODEL", "doubao-pro-32k"))


def get_llm_api_config(provider: str = None) -> dict:
    """API 调用：返回 LLM 配置（含 api_key）

    provider 缺省时使用当前默认 provider（LLM_PROVIDER 或 deepseek）。
    """
    provider = provider or settings.llm.provider
    cfg = settings.llm if provider == settings.llm.provider else LLMConfig(provider=provider)
    return {
        "provider": cfg.provider,
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "model": cfg.model,
    }


def get_llm_config(provider: str = None) -> LLMConfig:
    """根据 provider 返回对应的 LLM 配置

    所有 OpenAI 兼容协议的服务商统一由 LLMConfig 承载（含豆包/火山方舟，
    其 /api/v3 同样是 OpenAI 兼容端点）；未来接入非兼容协议（Claude/Gemini）
    时再在此扩展独立配置类。
    """
    provider = provider or settings.llm.provider
    if provider == settings.llm.provider:
        return settings.llm
    return LLMConfig(provider=provider)


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
