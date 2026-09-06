const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

export interface SupportedModel {
  value: string
  label: string
  provider: string
}

export interface LLMConfig {
  model: string
  api_key: string
  has_api_key: boolean
  provider: string
  base_url: string
  supported_models: SupportedModel[]
}

export const configApi = {
  async getLLM(): Promise<LLMConfig> {
    const res = await fetch(`${BASE_URL}/api/config/llm`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  async updateLLM(payload: { model: string; api_key: string }) {
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
