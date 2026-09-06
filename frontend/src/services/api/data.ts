const BASE_URL = ''

export const dataApi = {
  async generateSpec(description: string, files: Array<Record<string, unknown>>) {
    const res = await fetch(`${BASE_URL}/api/data/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, files }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ spec_md: string; tags: string[] }>
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

  async list(): Promise<{ datasets: Array<Record<string, unknown>> }> {
    const res = await fetch(`${BASE_URL}/api/data/list`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  async register(specMd: string, datasetName: string, dataPath: string, fileCount: number, totalSize: number, formats: string[], sourceFiles: string[], tags: string[] = []) {
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
        tags,
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

  async listFiles(datasetId: string) {
    const res = await fetch(`${BASE_URL}/api/data/${encodeURIComponent(datasetId)}/files`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json() as Promise<{ files: Array<{ name: string; path: string; format: string; size: number }>; count: number }>
  },
}
