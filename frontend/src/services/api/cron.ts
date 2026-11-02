/** 定时任务 API（后端 core/cron，SQLite storage/platform.db） */

export interface CronJob {
  id: string
  name: string
  kind: 'tool' | 'agent'
  target_id: string
  params: Record<string, unknown>
  cron: string
  timezone: string
  enabled: boolean
  owner: string
  timeout: number
  misfire: 'skip' | 'fire_once'
  next_run_at: number | null
  last_run_at: number | null
  last_status: string | null
  next_run_local?: string
  last_run_local?: string
}

export interface CronRun {
  id: string
  job_id: string
  started_at: number
  finished_at: number | null
  status: 'running' | 'success' | 'failed' | 'timeout' | 'skipped'
  trigger: 'schedule' | 'manual'
  message: string
  output_json: string
  error: string
  duration_ms: number | null
}

export interface CronOptions {
  tools: { id: string; name: string; description: string }[]
  agents: { id: string; name: string; description: string }[]
  timezones: string[]
}

export interface CronJobPayload {
  name: string
  kind: 'tool' | 'agent'
  target_id: string
  params: Record<string, unknown>
  cron: string
  timezone: string
  enabled: boolean
  timeout: number
  misfire: 'skip' | 'fire_once'
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      detail = body?.detail || (typeof body?.detail === 'string' ? body.detail : detail)
    } catch { /* 非 JSON 响应沿用状态码 */ }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const cronApi = {
  async list(): Promise<CronJob[]> {
    const data = await json<{ jobs: CronJob[] }>(await fetch('/api/cron/jobs'))
    return data.jobs
  },

  async options(): Promise<CronOptions> {
    return json<CronOptions>(await fetch('/api/cron/options'))
  },

  async create(payload: CronJobPayload): Promise<CronJob> {
    const data = await json<{ job: CronJob }>(
      await fetch('/api/cron/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    )
    return data.job
  },

  async update(jobId: string, payload: CronJobPayload): Promise<CronJob> {
    const data = await json<{ job: CronJob }>(
      await fetch(`/api/cron/jobs/${jobId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }),
    )
    return data.job
  },

  async remove(jobId: string): Promise<void> {
    await json(await fetch(`/api/cron/jobs/${jobId}`, { method: 'DELETE' }))
  },

  async toggle(jobId: string): Promise<CronJob> {
    const data = await json<{ job: CronJob }>(
      await fetch(`/api/cron/jobs/${jobId}/toggle`, { method: 'POST' }),
    )
    return data.job
  },

  async trigger(jobId: string): Promise<{ status: string }> {
    return json(await fetch(`/api/cron/jobs/${jobId}/trigger`, { method: 'POST' }))
  },

  async runs(jobId: string, limit = 20): Promise<CronRun[]> {
    const data = await json<{ runs: CronRun[] }>(
      await fetch(`/api/cron/jobs/${jobId}/runs?limit=${limit}`),
    )
    return data.runs
  },
}
