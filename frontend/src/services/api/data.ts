const BASE_URL = ''

export const dataApi = {
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

  async register(name: string, path: string, description: string = '') {
    const res = await fetch(`${BASE_URL}/api/data/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, path, description }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ dataset_id: string; entry: Record<string, unknown> }>
  },

  async delete(datasetId: string) {
    const res = await fetch(`${BASE_URL}/api/data/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })
    if (!res.ok) throw new Error((await res.json().catch(() => ({ detail: res.statusText }))).detail)
    return res.json() as Promise<{ success: boolean }>
  },
}
