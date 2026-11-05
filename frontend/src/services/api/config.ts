const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export interface SupportedModel {
  value: string
  label: string
  provider: string
}

export interface GenLLMConfig {
  /** 是否已启用独立的生成模型 */
  enabled: boolean
  /** true = 未配置，生成链跟随对话模型 */
  follow_global: boolean
  model: string
  model_name: string
  provider: string
  base_url: string
  api_key: string
  has_api_key: boolean
}

export interface ChatControlConfig {
  /** 平台控制熔断总开关；关闭后对话内一切平台操作返回「能力已停用」 */
  platform_control_enabled: boolean
  /** 每用户每分钟 platform 调用上限 */
  rate_limit_per_minute: number
  /** 生成类调用（创建工具/Agent）每 10 分钟上限 */
  gen_limit_per_10min: number
}

export interface LLMConfig {
  model: string
  api_key: string
  has_api_key: boolean
  provider: string
  base_url: string
  supported_models: SupportedModel[]
  timeout: number
  max_concurrency: number
  debug_exec_timeout: number
  debug_max_rounds: number
  agent_round_timeout: number
  agent_idle_timeout: number
  agent_cancel_grace: number
  gen: GenLLMConfig
  chat: ChatControlConfig
}

export interface LLMConfigUpdatePayload {
  model: string
  api_key: string
  timeout?: number
  max_concurrency?: number
  debug_exec_timeout?: number
  debug_max_rounds?: number
  /** Agent 单轮硬超时（秒）；0 = 不限制 */
  agent_round_timeout?: number
  /** Agent 单轮无输出空闲上限（秒）；0 = 不限制 */
  agent_idle_timeout?: number
  /** 温和取消等待窗口（秒） */
  agent_cancel_grace?: number
  rate_limit_per_minute?: number
  gen_limit_per_10min?: number
  platform_control_enabled?: boolean
  /** 生成模型：不传 = 不改动，'' = 关闭并跟随对话模型，其他 = `<provider>:<模型>` */
  gen_model?: string
  gen_api_key?: string
}

export const configApi = {
  async getLLM(): Promise<LLMConfig> {
    const res = await fetch(`${BASE_URL}/api/config/llm`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  async updateLLM(payload: LLMConfigUpdatePayload) {
    const res = await fetch(`${BASE_URL}/api/config/llm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail.detail || `HTTP ${res.status}`)
    }
    return res.json()
  },
}
