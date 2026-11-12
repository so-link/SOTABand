"""
全局配置
=========

系统全局配置，支持从环境变量、.env 文件加载。

配置优先级: 环境变量 > .env 文件 > 代码默认值
"""

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import Optional

# 自动加载项目根目录的 .env 文件
def _strip_inline_comment(value: str) -> str:
    """剥离 `.env` 行内注释。

    只把「空白 + # 到行尾」视为注释，且不碰被引号包裹的值——避免误伤
    ``p@ss#word`` 这类合法密码，或 ``"a # b"`` 这种显式引用。

    背景：历史 `.env.example` 把 ``# 超级用户密码…`` 写在 ``=` 同一行，
    原解析器整段当成值，导致超管密码/用户名被注释文本污染（登录即失败）。
    """
    raw = value.strip()
    if raw.startswith(('"', "'")):
        return raw
    return re.sub(r"\s+#.*$", "", raw).strip()


def _load_dotenv():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = _strip_inline_comment(val).strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

_load_dotenv()


# OpenAI 兼容协议的主流服务商预设表
# 任何遵循 OpenAI /v1/chat/completions 协议的服务（包括自建网关、第三方聚合平台）
# 都可以通过 <PROVIDER>_API_KEY / <PROVIDER>_BASE_URL / <PROVIDER>_MODEL 环境变量覆盖接入。
#
# 【关于 endpoint_variants —— 同一厂商可能有多个端点】
# 很多厂商按「计费方式 / 订阅套餐 / 站点区域」拆分出**互不通用的多套端点 + 多套 Key**，
# 例如 MiMo 的按量付费（sk- → api.xiaomimimo.com）与 Token Plan 订阅
# （tp- → token-plan-cn.xiaomimimo.com），官方明确说明两者不可混用。
# 若把端点写死成其中一个，用另一类 Key 调用必然鉴权失败。
#
# 因此每个厂商可登记若干 endpoint_variants：
#   base_url   该套方案的端点
#   label      人类可读名称（用于报错提示 / 前端展示）
#   key_prefix 该套方案 Key 的前缀；能区分时填（小写，如 "sk-" / "tp-"），
#              系统据此自动选端点，使用者无需关心；
#              无法按前缀区分时留空（如智谱通用端点 vs GLM Coding Plan 端点），
#              此时只能由使用者用 <PROVIDER>_BASE_URL / LLM_BASE_URL 显式指定。
#   docs_url   官方文档（查 Key / 查端点用）
#
# 解析顺序（见 resolve_base_url）：显式 base_url > Key 前缀匹配 > default_base_url
PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "name": "DeepSeek",
        "env_key": "DEEPSEEK_API_KEY",
        "default_base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-pro",
        "endpoint_variants": [
            {"base_url": "https://api.deepseek.com/v1", "label": "按量付费",
             "key_prefix": "sk-", "docs_url": "https://api-docs.deepseek.com/"},
        ],
    },
    "openai": {
        "name": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "endpoint_variants": [
            {"base_url": "https://api.openai.com/v1", "label": "官方 API",
             "key_prefix": "sk-", "docs_url": "https://platform.openai.com/docs/models"},
        ],
    },
    "moonshot": {
        "name": "Kimi (Moonshot)",
        "env_key": "MOONSHOT_API_KEY",
        "default_base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k2",
        # 中国站与国际站是两套独立账号体系，Key 不通用，但 Key 前缀同为 sk-
        # → 无法自动区分，国际站用户需显式设 MOONSHOT_BASE_URL
        "endpoint_variants": [
            {"base_url": "https://api.moonshot.cn/v1", "label": "中国站（默认）",
             "key_prefix": "", "docs_url": "https://platform.moonshot.cn/docs/guide/agent-support"},
            {"base_url": "https://api.moonshot.ai/v1", "label": "国际站",
             "key_prefix": "", "docs_url": "https://platform.moonshot.ai/docs/api/overview"},
        ],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "env_key": "ZHIPU_API_KEY",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
        # GLM Coding Plan 套餐 Key 与平台通用 Key 不通用，且端点也不同；
        # 两者 Key 格式同为 {id}.{secret}，无法按前缀区分 → 需显式设 ZHIPU_BASE_URL
        "endpoint_variants": [
            {"base_url": "https://open.bigmodel.cn/api/paas/v4", "label": "开放平台通用（默认）",
             "key_prefix": "", "docs_url": "https://docs.bigmodel.cn/cn/guide/start/quick-start"},
            {"base_url": "https://open.bigmodel.cn/api/coding/paas/v4", "label": "GLM Coding Plan 套餐",
             "key_prefix": "", "docs_url": "https://docs.bigmodel.cn/cn/coding-plan/tool/others"},
        ],
    },
    "qwen": {
        "name": "通义千问 (阿里云百炼)",
        "env_key": "DASHSCOPE_API_KEY",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        # 中国大陆版与国际版是两套独立账号，Key 不通用但前缀同为 sk-
        "endpoint_variants": [
            {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "label": "中国大陆版（默认）",
             "key_prefix": "", "docs_url": "https://help.aliyun.com/zh/model-studio/get-api-key"},
            {"base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "label": "国际版",
             "key_prefix": "", "docs_url": "https://help.aliyun.com/zh/model-studio/get-api-key"},
        ],
    },
    "siliconflow": {
        "name": "硅基流动",
        "env_key": "SILICONFLOW_API_KEY",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "default_model": "deepseek-ai/DeepSeek-V3",
        "endpoint_variants": [
            {"base_url": "https://api.siliconflow.cn/v1", "label": "官方 API",
             "key_prefix": "sk-", "docs_url": "https://docs.siliconflow.cn/"},
        ],
    },
    "minimax": {
        "name": "MiniMax",
        "env_key": "MINIMAX_API_KEY",
        "default_base_url": "https://api.minimaxi.com/v1",
        "default_model": "MiniMax-Text-01",
        # 中国版与全球版账号体系独立，Key 不通用，前缀无区分度
        "endpoint_variants": [
            {"base_url": "https://api.minimaxi.com/v1", "label": "中国版（默认）",
             "key_prefix": "", "docs_url": "https://platform.minimaxi.com/document/Models"},
            {"base_url": "https://api.minimax.io/v1", "label": "全球版",
             "key_prefix": "", "docs_url": "https://platform.minimax.io/document/Models"},
        ],
    },
    "mimo": {
        "name": "MiMo (小米)",
        "env_key": "MIMO_API_KEY",
        # 小米官方两套方案（见 https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call）：
        #   按量付费   sk- 前缀 → https://api.xiaomimimo.com/v1
        #   Token Plan tp- 前缀 → https://token-plan-cn.xiaomimimo.com/v1
        # 官方文档明确："两者相互独立，不可混用"。故默认取按量付费端点，
        # 检测到 tp- 前缀时自动切到 Token Plan 端点。
        "default_base_url": "https://api.xiaomimimo.com/v1",
        "endpoint_variants": [
            {"base_url": "https://api.xiaomimimo.com/v1", "label": "按量付费（默认）",
             "key_prefix": "sk-",
             "docs_url": "https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call"},
            {"base_url": "https://token-plan-cn.xiaomimimo.com/v1", "label": "Token Plan 订阅",
             "key_prefix": "tp-",
             "docs_url": "https://mimo.mi.com/docs/zh-CN/quick-start/faq/api-integration"},
        ],
        # 官方文档 curl 示例用小写 mimo-v2.5 / mimo-v2.5-pro，
        # 平台展示为 MiMo-V2.5 / MiMo-V2.5-Pro，两种写法服务端均可识别。
        "default_model": "mimo-v2.5",
    },
    "doubao": {
        "name": "豆包 (火山方舟)",
        "env_key": "DOUBAO_API_KEY",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-pro-32k",
        "endpoint_variants": [
            {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "label": "火山方舟",
             "key_prefix": "", "docs_url": "https://www.volcengine.com/docs/82379/1099320"},
        ],
    },
}


def list_endpoint_variants(provider: str) -> list[dict]:
    """列出某服务商的端点变体（不同计费方式 / 套餐 / 站点对应不同端点）。

    未知服务商或无变体登记时返回空列表。用于在连接失败时提示使用者
    「你的 Key 可能属于另一套端点」，避免对着 401 干瞪眼。
    """
    return list(PROVIDER_PRESETS.get(provider, {}).get("endpoint_variants", []))


def resolve_base_url(provider: str, api_key: str = "", fallback: str = "") -> str:
    """按 API Key 前缀自动选择服务商端点。

    同一厂商的多套方案（按量付费 / 订阅套餐 / 区域站点）往往使用不同端点，
    且 Key 互不通用。此处按 Key 前缀匹配 ``endpoint_variants``，
    让使用者只填 Key 就能命中正确端点，无需手写 base_url。

    **只对能按前缀区分的厂商生效**（如 MiMo 的 sk- / tp-）；
    前缀无区分度的厂商（如 Moonshot 中国站/国际站）返回 fallback，
    由使用者显式指定 <PROVIDER>_BASE_URL。

    Args:
        provider: 服务商 id
        api_key:  API Key（原始值，仅在本函数内做前缀比对，不外传不落盘）
        fallback: 无匹配时的回退端点（通常是预设的 default_base_url）

    Returns:
        解析出的端点；无匹配则返回 fallback。
    """
    key = (api_key or "").strip().lower()
    if not key:
        return fallback
    # 长前缀优先匹配，避免 "sk-" 抢先命中 "sk-ant-" 这类更具体的前缀
    variants = list_endpoint_variants(provider)
    candidates = [
        (v.get("key_prefix") or "").strip().lower()
        for v in variants
    ]
    for prefix in sorted((p for p in candidates if p), key=len, reverse=True):
        if key.startswith(prefix):
            for v in variants:
                if (v.get("key_prefix") or "").strip().lower() == prefix:
                    return v.get("base_url") or fallback
    return fallback


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
        base_url: 代码赋值 > LLM_BASE_URL(仅默认provider) > <PROVIDER>_BASE_URL
                  > 按 Key 前缀自动匹配 > 服务商预设默认值
        model   : 代码赋值 > LLM_MODEL(仅默认provider)    > <PROVIDER>_MODEL    > 服务商预设默认值

    关于端点自动匹配：同一厂商的不同计费/订阅方案可能使用**互不通用的多套
    端点 + 多套 Key**（典型如 MiMo：按量付费 sk- → api.xiaomimimo.com，
    Token Plan 订阅 tp- → token-plan-cn.xiaomimimo.com，官方声明不可混用）。
    未显式指定 base_url 时，系统按 Key 前缀自动挑选正确端点，详见
    PROVIDER_PRESETS 的 endpoint_variants 与 resolve_base_url()。
    前缀无区分度的厂商（Moonshot/Qwen/MiniMax 的中外站、智谱通用端点与
    GLM Coding Plan 端点）仍需用 <PROVIDER>_BASE_URL 显式指定。

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
    # Agent 代码生成需要 ~7 分钟，默认 1200（20 分钟）留足余量。
    timeout: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "1200")))

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
        # base_url: 代码赋值 > LLM_BASE_URL(默认provider) > <PROVIDER>_BASE_URL
        #           > 按 Key 前缀自动匹配（如 mimo 的 tp- / sk-）> 预设默认
        explicit_base_url = (
            self.base_url
            or global_base_url
            or os.getenv(f"{prefix}_BASE_URL", "")
        )
        self.base_url = explicit_base_url or resolve_base_url(
            self.provider, self.api_key, default_base_url
        )
        # model: 代码赋值 > LLM_MODEL(默认provider) > <PROVIDER>_MODEL > 预设默认
        self.model = (
            self.model
            or global_model
            or os.getenv(f"{prefix}_MODEL", default_model)
        )


def resolve_gen_llm_config() -> LLMConfig:
    """解析「生成/推理增强模型」配置（工具与 Agent 的规格/代码生成链专用）。

    背景：交互式对话的模型必须支持 function calling，而写代码/长逻辑推理
    往往想用另一家更强的模型。两者分开配置：
        对话（chat）  = settings.llm（LLM_PROVIDER / LLM_API_KEY / LLM_MODEL）
        生成（gen）   = LLM_GEN_* 环境变量，未配置 provider 时回退 settings.llm

    读取的变量（全部可选）：
        LLM_GEN_PROVIDER  服务商 id；留空 = 跟随全局对话模型
        LLM_GEN_MODEL     模型名；留空 = <PROVIDER>_MODEL 或服务商预设默认
        LLM_GEN_API_KEY   Key；留空 = <PROVIDER>_API_KEY（复用同厂商已有 Key）
        LLM_GEN_BASE_URL  端点；留空 = 按 Key 前缀自动匹配 / 服务商预设默认

    每次调用都重新读环境变量，因此 .env 改动后 _refresh_settings 同步
    os.environ 即可即时生效，不存在启动快照被固化的问题。
    """
    provider = os.getenv("LLM_GEN_PROVIDER", "").strip()
    if not provider:
        return settings.llm
    return LLMConfig(
        provider=provider,
        api_key=os.getenv("LLM_GEN_API_KEY", "").strip(),
        base_url=os.getenv("LLM_GEN_BASE_URL", "").strip(),
        model=os.getenv("LLM_GEN_MODEL", "").strip(),
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

    # ── 代码安全审查（core/security/review.py，移植自上游 a7b2db0）──
    # enforce: 静态违规阻止注册/执行，运行时违规强制失败
    # audit:   只记录违规，不拦截（首版默认，灰度观察误报率）
    # off:     完全关闭
    # 静态名单比 code_scan.py 严（subprocess.run/os.mkdir 等均 block）；
    # 内置工具命中已清零（回归测试锁定），可经 .env REVIEW_MODE 全局切 enforce，
    # 或先用 REVIEW_ENFORCE_TOOLS 按工具灰度（见 review_mode_for）。
    review_mode: str = field(default_factory=lambda: os.getenv("REVIEW_MODE", "audit"))
    # 分工具逐步 enforce（C-1 过渡形态）：全局 audit 时，此处列出的工具单独升级
    # enforce。逗号分隔工具 id，`*` = 全部。每次解析现读环境变量，改 .env 即生效。
    review_enforce_tools: str = field(
        default_factory=lambda: os.getenv("REVIEW_ENFORCE_TOOLS", ""))

    def review_mode_for(self, tool_id: str | None = None) -> str:
        """解析某工具实际生效的审查模式（off | audit | enforce）。

        优先级：全局 off / enforce 直接生效（off 保持一键全关语义）；
        全局 audit 时命中 REVIEW_ENFORCE_TOOLS（含 `*`）的工具升级为 enforce。
        每次调用现读环境变量，运行中调整即生效。
        """
        mode = (os.getenv("REVIEW_MODE", "") or self.review_mode or "audit").strip().lower()
        if mode in ("off", "enforce"):
            return mode
        if tool_id:
            raw = os.getenv("REVIEW_ENFORCE_TOOLS", "") or self.review_enforce_tools
            entries = {t.strip() for t in raw.split(",") if t.strip()}
            if "*" in entries or tool_id in entries:
                return "enforce"
        return "audit"
    # 运行时文件写入白名单（绝对路径或相对项目根）。
    # 用户目录 <USER_HOME>/<uid>/ 与系统临时目录由执行器自动追加，
    # 不必在此声明（本地工具产物按多用户模型必须落用户目录）。
    allowed_write_paths: list = field(default_factory=lambda: ["data", "logs", "output"])
    # 运行时网络访问白名单（主机名）；空列表 = 网络不拦截，仅记录
    allowed_network_hosts: list = field(default_factory=list)
    runtime_enforce_network: bool = False
    # 子进程资源限制（**0 = 不限制**，与超时体系「0=不限制」约定一致）。
    # 默认全关：RLIMIT_AS 按虚拟内存计会误杀 torch/CUDA 类工具；
    # RLIMIT_NPROC 按 uid 全局计数，本机服务同 uid 运行时会掐断
    # _call_tool 派生。超时兜底走唯一入口 TOOL_EXEC_TIMEOUT，不用这里。
    max_cpu_seconds: int = 0
    max_memory_mb: int = 0
    max_file_size_mb: int = 0
    max_processes: int = 0


@dataclass
class ObservabilityConfig:
    """可观测性配置"""

    metrics_enabled: bool = True
    metrics_port: int = 9090
    tracing_enabled: bool = True
    tracing_backend: str = "otel"  # otel | jaeger | zipkin
    alert_webhook_url: Optional[str] = None


# ── 调试执行阈值：全系统唯一读取入口 ─────────────────────────────
# 工具调试、Agent 调试、modify-and-debug 三处必须从这里取值。
# 此前各自维护默认值（60 / 180）并在 modify-and-debug 里硬编码 30，
# 导致「系统设置」里改大了超时、实际执行仍被小值掐断。
DEBUG_EXEC_TIMEOUT_DEFAULT = 60.0
DEBUG_MAX_ROUNDS_DEFAULT = 10


def get_debug_exec_timeout() -> float:
    """自动调试单轮沙箱执行超时（秒），读 .env 的 DEBUG_EXEC_TIMEOUT。

    每次调用现读环境变量，因此「系统设置」保存后（写 .env + 同步
    os.environ）无需重启即对下一次调试生效。
    被调试的工具/Agent 代码若陷入死循环，没有超时会让整条调试流永久挂起。
    """
    try:
        return max(5.0, float(os.getenv("DEBUG_EXEC_TIMEOUT", "") or DEBUG_EXEC_TIMEOUT_DEFAULT))
    except (TypeError, ValueError):
        return DEBUG_EXEC_TIMEOUT_DEFAULT


def get_debug_max_rounds() -> int:
    """自动调试最大轮数，读 .env 的 DEBUG_MAX_ROUNDS；超过即停止。"""
    try:
        return max(1, int(os.getenv("DEBUG_MAX_ROUNDS", "") or DEBUG_MAX_ROUNDS_DEFAULT))
    except (TypeError, ValueError):
        return DEBUG_MAX_ROUNDS_DEFAULT


@dataclass
class ChatConfig:
    """对话内平台控制（docs/chat-platform-control-design.md §6.5/§6.6）"""

    # 熔断总开关：关闭后网关对一切 platform 调用返回"能力已停用"
    platform_control_enabled: bool = field(
        default_factory=lambda: os.getenv("SOTABAND_PLATFORM_CONTROL", "1").lower()
        not in ("0", "false", "no")
    )
    # 每用户每分钟 platform-call 上限
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.getenv("SOTABAND_CHAT_RATE_LIMIT_PER_MIN", "30") or 30)
    )
    # 生成类工具（create_tool/create_agent）成本更高，单独更严的限制。
    # 注意单次生成要跑数分钟，窗口内 5 次实际很容易触顶，可按需放宽。
    gen_limit_per_10min: int = field(
        default_factory=lambda: int(os.getenv("SOTABAND_CHAT_GEN_LIMIT_PER_10MIN", "5") or 5)
    )


@dataclass
class AppConfig:
    """应用配置（由模块顶部 _load_dotenv 预加载的 .env 环境变量驱动）"""

    app_name: str = "SOTABand"
    debug: bool = field(default_factory=lambda: os.getenv("SOTABAND_DEBUG", "").lower() in ("1", "true", "yes"))
    # 默认只监听回环：代码执行/文件接口无鉴权时，绑 0.0.0.0 等于把 RCE 暴露给全网。
    # 服务器部署须显式设置 SOTABAND_API_HOST 并配置鉴权。
    api_host: str = field(default_factory=lambda: os.getenv("SOTABAND_API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: int(os.getenv("SOTABAND_API_PORT", "8201")))
    web_port: int = field(default_factory=lambda: int(os.getenv("SOTABAND_WEB_PORT", "8200")))
    # CORS 允许的来源。默认空列表 = 仅放行同源/无 Origin 请求（curl、服务端、
    # 以及经 vite proxy / nginx 反代的同源 /api 调用）。跨域访问需显式配置：
    #   SOTABAND_CORS_ORIGINS="https://a.example.com,https://b.example.com"
    # 历史上这里是 ["*"]，与 allow_credentials=True 自相矛盾，随身份层收紧。
    cors_origins: list = field(default_factory=lambda: [
        o.strip() for o in os.getenv("SOTABAND_CORS_ORIGINS", "").split(",") if o.strip()
    ])


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
    chat: ChatConfig = field(default_factory=ChatConfig)


# 全局单例
settings = Settings()
