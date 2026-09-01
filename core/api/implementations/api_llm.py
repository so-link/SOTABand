"""LLM 相关 API 实现"""
from core.llm.client import create_llm_client
from config.settings import get_llm_api_config, PROVIDER_PRESETS
from core.llm import providers as provider_catalog
from core.llm.providers import CAP_REASONING
from core.security.secrets import mask_secret, scrub_text


class ApiLlmChat:
    """api-llm-chat: LLM 非流式对话"""

    @staticmethod
    async def call(**kwargs) -> dict:
        client = create_llm_client()
        content = await client.chat(
            messages=kwargs.get("messages", []),
            temperature=kwargs.get("temperature", 0.7),
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        return {"content": content}


class ApiLlmTestConfig:
    """api-llm-test-config: 一键验证自定义配置是否可用

    使用者填完「供应商 / 模型 / Key」后，正式使用前先验证：
    - 端点能否连通
    - Key 是否有效
    - 填的模型是否真实存在（这是最容易填错的一项）
    - 顺带列出该 Key 当前可用的全部模型

    与 api-llm-test-connection 的区别：后者只测项目 .env 的全局配置；
    本 API 测的是调用方临时传入的配置，不影响全局。

    输入：
        provider / base_url（二选一，用于解析端点）
        api_key（必填）
        model（可选）：填了就顺带校验该模型是否存在

    输出：
        ok          是否连通且鉴权通过
        model_valid 所填模型是否存在（仅在传了 model 且列表拉取成功时给出）
        models      该 Key 可用的模型列表（每项含能力标注）
        suggestion  模型不存在时的最接近匹配建议
    """

    @staticmethod
    async def call(**kwargs) -> dict:
        from core.security.secrets import mask_secret as _mask

        api_key = (kwargs.get("api_key") or "").strip()
        provider = (kwargs.get("provider") or "").strip()
        base_url = (kwargs.get("base_url") or "").strip()
        model = (kwargs.get("model") or "").strip()

        if not api_key:
            return {"ok": False, "error": "未提供 api_key"}

        # 带上 api_key：同一厂商可能有多套互不通用的端点，按 Key 前缀自动选择
        resolved_provider, resolved_url = provider_catalog.resolve(
            provider=provider, base_url=base_url, api_key=api_key
        )
        if not resolved_url:
            return {
                "ok": False,
                "error": f"无法解析服务地址（provider={provider or '未填'}）。"
                         f"已知服务商：{', '.join(p['id'] for p in provider_catalog.list_providers())}",
            }

        result = await provider_catalog.fetch_remote_models(
            provider=resolved_provider, base_url=resolved_url, api_key=api_key
        )

        out = {
            "ok": bool(result.get("ok")),
            "provider": resolved_provider,
            "base_url": resolved_url,
            "api_key_masked": _mask(api_key),
        }

        if not result.get("ok"):
            out["error"] = result.get("error", "连接失败")
            # 首选端点失败 → 该 Key 可能属于同一厂商的另一套端点
            # （如 MiMo 按量付费 sk- 与 Token Plan 订阅 tp- 互不通用的两套）。
            # 逐个试一遍，命中就直接切换到可用端点，省去人工试错。
            alt, alt_url, tried = await _probe_endpoint_variants(
                resolved_provider, resolved_url, api_key
            )
            if alt is not None:
                result = alt
                resolved_url = alt_url
                out["ok"] = True
                out["base_url"] = alt_url
                del out["error"]
                out["endpoint_note"] = (
                    f"当前 Key 不属于默认端点，已自动改用 {alt_url}。"
                    f"建议把该端点写入 .env（LLM_BASE_URL 或 "
                    f"<PROVIDER>_BASE_URL），以免每次多试一次。"
                )
            else:
                out["tried_endpoints"] = [
                    {"base_url": resolved_url, "label": "默认",
                     "error": out["error"]},
                    *tried,
                ]
                endpoint_hint = provider_catalog.format_endpoint_hint(
                    resolved_provider, exclude=resolved_url
                )
                out["hint"] = (
                    (endpoint_hint + " ") if endpoint_hint else ""
                ) + (
                    "常见原因：Key 无效或已过期、Key 与端点不匹配"
                    "（例如把按量付费的 Key 用到了订阅套餐专用端点）、"
                    "该 Key 无此模型权限、网络不通。"
                )
                return out

        models = result.get("models", [])
        out["models"] = models
        out["count"] = len(models)

        # 校验使用者填的模型是否存在
        if model:
            ids = [m["id"] for m in models]
            exact = model in ids
            out["model_valid"] = exact
            if not exact:
                # 给出最接近的匹配，而不是让用户对着几十个模型名找
                out["suggestion"] = _closest_match(model, ids)
                out["message"] = (
                    f"模型 '{model}' 不在可用列表中。"
                    f"最接近的是：{out['suggestion'] or '（无相似项）'}"
                )
            else:
                caps = provider_catalog._lookup_capabilities(resolved_provider, model)
                out["capabilities"] = caps
                if CAP_REASONING in caps:
                    out["hint"] = "该模型为推理模型，max_tokens 建议 >= 1500，否则返回空内容"
        return out


# 这些错误特征通常意味着「Key 与端点不是同一套方案」，而非 Key 本身无效
_ENDPOINT_MISMATCH_MARKERS = (
    "401", "403", "unauthorized", "authentication", "forbidden",
    "invalid api key", "invalid_api_key", "invalid token", "invalid_token",
    "incorrect api key", "authentication_error", "permission",
    "model not found", "model does not exist", "no such model",
    "invalid model", "not_found", "404",
)


def _looks_like_endpoint_mismatch(err_text: str) -> bool:
    """判断失败是否疑似「Key 与端点不匹配」。

    命中时才值得换端点重试；超时、限流、内容审核等错误重试无意义。
    """
    low = (err_text or "").lower()
    return any(m in low for m in _ENDPOINT_MISMATCH_MARKERS)


async def _retry_chat_on_other_endpoints(provider: str, tried_url: str,
                                         api_key: str, model: str,
                                         messages: list,
                                         temperature: float,
                                         max_tokens: int) -> dict | None:
    """用该厂商的其他端点重试一次对话，成功则返回结果，全失败返回 None。

    仅由 ``_looks_like_endpoint_mismatch`` 判定后才调用。
    """
    from config.settings import LLMConfig

    for variant in provider_catalog.describe_endpoint_variants(
        provider, exclude=tried_url
    ):
        alt_url = (variant.get("base_url") or "").strip()
        if not alt_url:
            continue
        alt_cfg = LLMConfig(
            provider=provider or "custom",
            api_key=api_key,
            base_url=alt_url,
            model=model,
        )
        client = None
        try:
            client = create_llm_client(alt_cfg)
            content = await client.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "content": content,
                "provider": alt_cfg.provider,
                "model": model,
                "base_url": alt_url,
                "api_key_masked": mask_secret(api_key),
                "endpoint_note": (
                    f"当前 Key 不属于默认端点，已自动改用 {alt_url}。"
                    f"建议把该端点写入配置（<PROVIDER>_BASE_URL 或 "
                    f"LLM_BASE_URL），以免每次多试一次。"
                ),
            }
        except Exception:
            continue
        finally:
            # 用完即关：不保留连接，也不在进程内留下可用凭据
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
    return None


async def _probe_endpoint_variants(provider: str, tried_url: str,
                                   api_key: str) -> tuple[dict | None, str, list]:
    """首选端点失败后，逐个尝试该厂商的其他端点，返回第一个可用的。

    背景：同一厂商往往有多套方案（按量付费 / 订阅套餐 / 区域站点），
    每套端点不同且 Key 互不通用。用错端点时服务端只回 401「invalid api key」，
    使用者根本看不出该换哪个端点。这里自动试一遍，命中就直接给出答案。

    Returns:
        (result, base_url, tried)
        - result 为 None 表示全部失败；否则是首个成功端点的拉取结果
        - base_url 为成功端点（失败时为原 tried_url）
        - tried 为已尝试过的失败端点列表，便于一次性呈现
    """
    tried: list[dict] = []
    variants = provider_catalog.describe_endpoint_variants(
        provider, exclude=tried_url
    )
    for variant in variants:
        alt_url = (variant.get("base_url") or "").strip()
        if not alt_url:
            continue
        alt = await provider_catalog.fetch_remote_models(
            provider=provider, base_url=alt_url, api_key=api_key
        )
        if alt.get("ok"):
            return alt, alt_url, tried
        tried.append({
            "base_url": alt_url,
            "label": variant.get("label", ""),
            "error": alt.get("error", "连接失败"),
        })
    return None, tried_url, tried


def _closest_match(target: str, candidates: list[str]) -> str | None:
    """在候选列表中找与目标最接近的字符串（简单编辑距离）"""
    if not candidates:
        return None

    def dist(a: str, b: str) -> int:
        # 限制长度以控制开销；模型名通常不长
        a, b = a[:64], b[:64]
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                              prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]

    t = (target or "").lower()
    best, best_d = None, 10**9
    for c in candidates:
        d = dist(t, c.lower())
        if d < best_d:
            best, best_d = c, d
    # 差异过大则认为没有相似项，避免给出误导性建议
    return best if best_d <= max(4, len(t) // 2) else None


class ApiLlmListProviders:
    """api-llm-list-providers: 查询可用的服务商目录

    让工具/界面能回答"我想用多模态，该选哪家？"这类问题，并拿到
    该服务商的推荐模型列表与官方文档地址。

    与 api-llm-chat-with-config 配合：先查目录选出 provider，
    再把 provider 传给它，base_url 由系统自动解析，无需使用者填写。

    输入：
        capability（可选）：按能力过滤，如 "vision" 只要支持图像输入的
        provider（可选）：只查指定服务商的详情
        api_key（可选）：给了则按 Key 前缀返回该 Key 实际会命中的端点

    输出：
        providers: 服务商列表，每项含 id/name/capabilities/models/
                   endpoint_variants/key_url/docs_url/notes
                   （endpoint_variants 列出该厂商多套互不通用的端点及适用 Key 前缀）
    """

    @staticmethod
    async def call(**kwargs) -> dict:
        capability = (kwargs.get("capability") or "").strip() or None
        provider = (kwargs.get("provider") or "").strip()
        api_key = (kwargs.get("api_key") or "").strip()

        if provider:
            meta = provider_catalog.get_provider_meta(provider)
            base_url = provider_catalog.get_base_url(provider, api_key=api_key)
            if not meta and not base_url:
                return {
                    "providers": [],
                    "error": f"未知服务商 '{provider}'。"
                             f"可用：{', '.join(p['id'] for p in provider_catalog.list_providers())}",
                }
            preset = PROVIDER_PRESETS.get(provider, {})
            return {
                "providers": [{
                    "id": provider,
                    "name": preset.get("name", provider),
                    "base_url": base_url,
                    "capabilities": meta.get("capabilities", []),
                    "models": meta.get("models", []),
                    "default_model": preset.get("default_model", ""),
                    "endpoint_variants": provider_catalog.describe_endpoint_variants(
                        provider
                    ),
                    "key_url": meta.get("key_url", ""),
                    "docs_url": meta.get("docs_url", ""),
                    "notes": meta.get("notes", ""),
                }]
            }

        return {"providers": provider_catalog.list_providers(capability=capability)}


class ApiLlmChatWithConfig:
    """api-llm-chat-with-config: 用调用方临时指定的配置调用 LLM

    与 api-llm-chat 的区别：api-llm-chat 固定使用项目 .env 的全局配置；
    本 API 允许单次调用时覆盖服务商、端点、模型与密钥。

    典型场景：项目默认用 deepseek-v4-flash（纯文本），但某个工具需要多模态
    能力，必须换成支持视觉的模型（如 hy4 / 豆包 vision）。此时无需改动
    全局 .env，只在该工具内传入自己的配置即可。

    【安全约束】
    - api_key 只在本次调用的内存中使用，不写入任何配置文件、注册表或日志
    - 返回的 api_key_masked 是脱敏值，永不返回原始 key
    - 异常信息统一脱敏，避免 key 随报错文本外泄
    - 调用结束后立即关闭 client，不保留任何连接池

    【必填/选填】
    - 必填：messages、api_key、model
    - 选填：base_url（缺省时按 provider 查预设表）、provider（仅用于查预设）
    """

    @staticmethod
    async def call(**kwargs) -> dict:
        from config.settings import LLMConfig

        messages = kwargs.get("messages", [])
        api_key = (kwargs.get("api_key") or "").strip()
        model = (kwargs.get("model") or "").strip()
        base_url = (kwargs.get("base_url") or "").strip()
        provider = (kwargs.get("provider") or "").strip()

        if not messages:
            return {"content": "", "error": "messages 不能为空"}
        if not api_key:
            return {"content": "", "error": "未提供 api_key"}
        if not model:
            return {"content": "", "error": "未提供 model"}

        # base_url 自动解析（使用者无需知道各家的端点地址）：
        #   1) 显式给了 base_url  → 自定义模式，直接采用
        #   2) 给了 provider      → 查服务商目录（按 Key 前缀在多套端点间自动选择）
        #   3) 只给了 model       → 按模型名推断服务商（如 gpt-4o → openai）
        resolved_provider, resolved_base_url = provider_catalog.resolve(
            provider=provider, model=model, base_url=base_url, api_key=api_key
        )

        if not resolved_base_url:
            # 解析失败时，把可选的服务商告诉调用方，而不是让他去猜
            known = provider_catalog.list_providers()
            hint = "\n".join(f"  - {p['id']}: {p['name']}" for p in known)
            return {
                "content": "",
                "error": (
                    f"无法自动解析服务地址：provider={provider or '(未填)'}、"
                    f"model={model or '(未填)'} 都无法匹配到已知服务商。\n"
                    f"请传入 provider（推荐）或显式传入 base_url。\n"
                    f"已知服务商：\n{hint}"
                ),
            }

        cfg = LLMConfig(
            provider=provider or "custom",
            api_key=api_key,
            base_url=resolved_base_url,
            model=model,
        )

        client = None
        try:
            client = create_llm_client(cfg)
            content = await client.chat(
                messages=messages,
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 4096),
            )
            return {
                "content": content,
                "provider": cfg.provider,
                "model": model,
                "base_url": resolved_base_url,
                # 只回传脱敏值，原始 key 不出现在返回结果中
                "api_key_masked": mask_secret(api_key),
            }
        except Exception as e:
            # 脱敏后再返回，防止 key 被拼进异常文本
            err_text = scrub_text(str(e), max_len=500)
            # Key 与端点不匹配是最高频的失败原因（如把按量付费 Key 用到
            # 订阅套餐专用端点）。这类错误自动换端点重试一次；其他错误
            # （网络超时、内容审核等）不重试，避免掩盖真实问题。
            if not base_url and _looks_like_endpoint_mismatch(err_text):
                retried = await _retry_chat_on_other_endpoints(
                    provider=resolved_provider,
                    tried_url=resolved_base_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    temperature=kwargs.get("temperature", 0.7),
                    max_tokens=kwargs.get("max_tokens", 4096),
                )
                if retried is not None:
                    return retried
            out = {
                "content": "",
                "error": f"调用失败: {err_text}",
                "provider": cfg.provider,
                "model": model,
                "base_url": resolved_base_url,
                "api_key_masked": mask_secret(api_key),
            }
            endpoint_hint = provider_catalog.format_endpoint_hint(
                resolved_provider, exclude=resolved_base_url
            )
            if endpoint_hint:
                out["endpoint_hint"] = endpoint_hint
            return out
        finally:
            # 用完即关：不保留连接，也不在进程内留下可用凭据
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass


class ApiLlmChatStream:
    """api-llm-chat-stream: LLM 流式对话"""

    @staticmethod
    async def call(**kwargs) -> dict:
        # 流式需要特殊处理，这里返回生成器标记
        return {"stream": True, "messages": kwargs.get("messages", []),
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 4096)}


class ApiLlmGetConfig:
    """api-llm-get-config: 获取 LLM 配置"""

    @staticmethod
    def call(**kwargs) -> dict:
        provider = kwargs.get("provider", "deepseek")
        return get_llm_api_config(provider)


class ApiDoubaoGetKey:
    """api-doubao-get-key: 获取豆包 API Key"""

    @staticmethod
    def call(**kwargs) -> dict:
        config = get_llm_api_config("doubao")
        return {"api_key": config.get("api_key", "")}


class ApiDeepseekGetKey:
    """api-deepseek-get-key: 获取 DeepSeek API Key 和模型名称（从 .env 读取）"""

    @staticmethod
    def call(**kwargs) -> dict:
        return get_llm_api_config("deepseek")


class ApiLlmTestConnection:
    """api-llm-test-connection: 测试 LLM 连接

    发送一个极短请求验证 API Key / Base URL / Model 是否配置正确。
    未显式传入的参数自动从 .env / 预设表读取（优先级见 LLMConfig），
    因此无需切换默认 provider 也能单独测试某个服务商。
    """

    @staticmethod
    async def call(**kwargs) -> dict:
        import time

        from config.settings import LLMConfig
        from core.llm.client import create_llm_client

        # provider 未指定 → 用当前默认；api_key/base_url/model 未指定 → 走 .env/预设
        test_cfg = LLMConfig(
            provider=kwargs.get("provider"),
            api_key=kwargs.get("api_key") or "",
            base_url=kwargs.get("base_url") or "",
            model=kwargs.get("model") or "",
        )
        # 测试用短超时，配错时快速失败
        test_cfg.timeout = 10

        if not test_cfg.api_key:
            return {"ok": False, "provider": test_cfg.provider, "model": test_cfg.model,
                    "base_url": test_cfg.base_url, "latency_ms": None,
                    "message": "未配置 API Key（LLM_API_KEY 或对应服务商的环境变量为空）"}
        if not test_cfg.base_url:
            return {"ok": False, "provider": test_cfg.provider, "model": test_cfg.model,
                    "base_url": "", "latency_ms": None,
                    "message": "未配置 API 端点（LLM_BASE_URL，或该服务商没有预设端点）"}
        if not test_cfg.model:
            return {"ok": False, "provider": test_cfg.provider, "model": test_cfg.model,
                    "base_url": test_cfg.base_url, "latency_ms": None,
                    "message": "未配置模型名（LLM_MODEL，或该服务商没有预设模型）"}
        # 记录最终端点供失败提示使用；LLMConfig 已按 Key 前缀自动选过端点，
        # 若 Key 与端点不属于同一套方案，这里仍可能鉴权失败。
        endpoint_hint = provider_catalog.format_endpoint_hint(test_cfg.provider)

        start = time.monotonic()
        try:
            client = create_llm_client(test_cfg)
            # 极短 ping：只生成 1 个 token，用于验证连通性
            await client.chat(
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return {
                "ok": True,
                "provider": test_cfg.provider,
                "model": test_cfg.model,
                "base_url": test_cfg.base_url,
                "api_key_masked": _mask_key(test_cfg.api_key),
                "latency_ms": latency_ms,
                "message": f"连接成功（{latency_ms}ms），{test_cfg.provider}/{test_cfg.model} 可用",
            }
        except Exception as e:  # noqa: BLE001 - 需要捕获所有连接错误做分类提示
            latency_ms = int((time.monotonic() - start) * 1000)
            err = str(e)
            err_lower = err.lower()
            if any(k in err_lower for k in ("api key", "authentication", "401", "403", "unauthorized")):
                hint = "API Key 无效或未授权，请检查 LLM_API_KEY"
                # Key 与端点不属于同一套方案时，服务端同样只回「无效 Key」
                if endpoint_hint:
                    hint = f"{hint}。{endpoint_hint}"
            elif any(k in err_lower for k in ("model not found", "model does not exist",
                                              "no such model", "invalid model", "model_name",
                                              "404", "not_found")):
                hint = "模型名不正确，或当前套餐不包含该模型，请检查 LLM_MODEL"
                if endpoint_hint:
                    hint = f"{hint}。{endpoint_hint}"
            elif any(k in err_lower for k in ("connection", "resolve", "timed out", "timeout",
                                              "network", "ssl", "econnrefused", "dns")):
                hint = "无法连接，请检查 LLM_BASE_URL 与网络"
            else:
                hint = err
            return {
                "ok": False,
                "provider": test_cfg.provider,
                "model": test_cfg.model,
                "base_url": test_cfg.base_url,
                "api_key_masked": _mask_key(test_cfg.api_key),
                "latency_ms": latency_ms,
                "message": hint,
            }


def _mask_key(key: str) -> str:
    """API Key 脱敏显示（保留前 4 后 4）"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"
