/** LLM 配置 API —— 全部走统一 client（`./client`）。 */
import { apiGet, apiPost } from './client'

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
    return apiGet<LLMConfig>('/api/config/llm')
  },

  async updateLLM(payload: { model: string; api_key: string }) {
    return apiPost<LLMConfig>('/api/config/llm', payload)
  },
}
