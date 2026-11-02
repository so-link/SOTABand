/** 任务历史 API（后端 SQLite storage/platform.db） */
const BASE_URL = ''

export interface TaskRecord {
  id: number
  agent_id: string
  agent_name: string
  session_id: string
  input: string
  status: 'running' | 'success' | 'failed'
  started_at: number
  finished_at: number | null
  duration_ms: number | null
  error: string
}

export interface RecentAgentRecord {
  agent_id: string
  last_at: number
  use_count: number
}

export const taskApi = {
  async list(limit = 30, agentId?: string): Promise<TaskRecord[]> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (agentId) params.set('agent_id', agentId)
    const res = await fetch(`${BASE_URL}/api/task/list?${params}`)
    if (!res.ok) throw new Error(`任务历史加载失败 (${res.status})`)
    const data = await res.json()
    return (data.tasks ?? []) as TaskRecord[]
  },

  async recentAgents(limit = 10): Promise<RecentAgentRecord[]> {
    const res = await fetch(`${BASE_URL}/api/task/recent-agents?limit=${limit}`)
    if (!res.ok) return []
    const data = await res.json()
    return (data.agents ?? []) as RecentAgentRecord[]
  },
}
