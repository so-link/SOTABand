const BASE_URL = ''

export const dataApi = {
  async generateSpec(description: string, files: Array<Record<string, unknown>>) {
    const res = await fetch(`${BASE_URL}/api/data/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, files }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ spec_md: string }>
  },

  async scanDirectory(path: string) {
    const res = await fetch(`${BASE_URL}/api/data/scan-directory`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<Record<string, unknown>>
  },

  async load(datasetId: string) {
    const res = await fetch(`${BASE_URL}/api/data/load?dataset=${encodeURIComponent(datasetId)}`)
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<Record<string, unknown>>
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
    const url = `${BASE_URL}/api/data/list?available_only=${availableOnly ? 'true' : 'false'}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  async register(specMd: string, datasetName: string, dataPath: string, fileCount: number, totalSize: number, formats: string[], sourceFiles: string[]) {
    const res = await fetch(`${BASE_URL}/api/data/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        specMd,
        datasetName,
        dataPath,
        fileCount,
        totalSize,
        formats,
        sourceFiles,
      }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || err.message || '注册失败')
    }
    return res.json() as Promise<{ dataset_id: string; entry: Record<string, unknown> }>
  },

  async delete(datasetId: string) {
    const res = await fetch(`${BASE_URL}/api/data/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ success: boolean }>
  },
}
