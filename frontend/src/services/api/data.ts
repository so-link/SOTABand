/** 数据集 API 服务 —— 全部走统一 client（`./client`）。 */
import { apiDelete, apiGet, apiPost } from './client'

export const dataApi = {
  async generateSpec(description: string, files: Array<Record<string, unknown>>) {
    return apiPost<{ spec_md: string }>('/api/data/generate-spec', { description, files })
  },

  async scanDirectory(path: string) {
    return apiPost<Record<string, unknown>>('/api/data/scan-directory', { path })
  },

  async load(datasetId: string) {
    return apiGet<Record<string, unknown>>(
      `/api/data/load?dataset=${encodeURIComponent(datasetId)}`
    )
  },

  /**
   * 列出数据集。
   * @param availableOnly 默认 true：只返回本机真实存在数据的数据集。
   *   注册表常包含他人环境的条目（data_path 指向对方机器），默认过滤掉，
   *   避免展示能选中、但一运行就报“路径不存在”的幽灵数据集。
   */
  async list(availableOnly: boolean = true): Promise<{
    datasets: Array<Record<string, unknown>>
    total: number
    available_count: number
    unavailable_count: number
  }> {
    return apiGet(
      `/api/data/list?available_only=${availableOnly ? 'true' : 'false'}`
    )
  },

  async register(specMd: string, datasetName: string, dataPath: string, fileCount: number, totalSize: number, formats: string[], sourceFiles: string[]) {
    return apiPost<{ dataset_id: string; entry: Record<string, unknown> }>(
      '/api/data/register',
      { specMd, datasetName, dataPath, fileCount, totalSize, formats, sourceFiles }
    )
  },

  async delete(datasetId: string) {
    return apiDelete<{ success: boolean }>(`/api/data/${encodeURIComponent(datasetId)}`)
  },

  async listFiles(datasetId: string) {
    return apiGet<{
      files: Array<{ name: string; path: string; format: string; size: number }>
      count: number
    }>(`/api/data/${encodeURIComponent(datasetId)}/files`)
  },
}
