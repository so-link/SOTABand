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
  gen: GenLLMConfig
}

export interface LLMConfigUpdatePayload {
  model: string
  api_key: string
  timeout?: number
  max_concurrency?: number
  debug_exec_timeout?: number
  debug_max_rounds?: number
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
