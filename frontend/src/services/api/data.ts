const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'

export const dataApi = {
  async scanDirectory(path: string) {
    const res = await fetch(`${BASE_URL}/api/data/scan-directory`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<Record<string, unknown>>
  },

  async generateSpec(description: string, files: Array<Record<string, unknown>>) {
    const res = await fetch(`${BASE_URL}/api/data/generate-spec`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description, files }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ spec_md: string }>
  },

  async register(specMd: string, datasetName: string, dataPath: string, fileCount: number, totalSize: number, formats: string[]) {
    const idMatch = specMd.match(/^id:\s*(.+)$/m)
    const res = await fetch(`${BASE_URL}/api/data/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specMd, datasetId: idMatch?.[1]?.trim() || '', datasetName, dataPath, fileCount, totalSize, formats }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ dataset_id: string; entry: Record<string, unknown> }>
  },

  async list() {
    const res = await fetch(`${BASE_URL}/api/data/list`)
    return res.json() as Promise<{ datasets: Array<Record<string, unknown>> }>
  },

  async get(id: string) {
    const res = await fetch(`${BASE_URL}/api/data/${id}`)
    if (!res.ok) throw new Error('Not found')
    return res.json() as Promise<Record<string, unknown>>
  },

  async matchTools(datasetId: string, request: string) {
    const res = await fetch(`${BASE_URL}/api/data/match-tools`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ datasetId, request }),
    })
    return res.json() as Promise<{ matches: string[]; reason: string; total_tools: number }>
  },

  async preview(datasetId: string) {
    const res = await fetch(`${BASE_URL}/api/data/${datasetId}/preview`)
    return res.json() as Promise<Record<string, unknown>>
  },
}
