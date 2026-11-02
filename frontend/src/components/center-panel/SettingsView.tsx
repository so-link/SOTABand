import { useState, useEffect } from 'react'
import { Settings, Cpu, KeyRound, Loader2, CheckCircle2, AlertCircle, Eye, EyeOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardBody } from '@/components/ui/card'
import { configApi, type LLMConfig, type SupportedModel } from '@/services/api/config'

export function SettingsView() {
  const [config, setConfig] = useState<LLMConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState('')
  // 运行参数（可在界面调整，保存后写入 .env 并即时生效）
  const [llmTimeout, setLlmTimeout] = useState('1200')
  const [maxConcurrency, setMaxConcurrency] = useState('1')
  const [debugExecTimeout, setDebugExecTimeout] = useState('60')
  const [debugMaxRounds, setDebugMaxRounds] = useState('10')
  // 生成/推理增强模型（可选）：'' 表示跟随上面的对话模型
  const [genModel, setGenModel] = useState('')
  const [genApiKey, setGenApiKey] = useState('')
  const [showGenKey, setShowGenKey] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const cfg = await configApi.getLLM()
        if (cancelled) return
        setConfig(cfg)
        setModel(cfg.model)
        // 不填入脱敏后的 key，保持空；placeholder 提示「已配置，输入新值以替换」
        setApiKey('')
        setLlmTimeout(String(cfg.timeout ?? 1200))
        setMaxConcurrency(String(cfg.max_concurrency ?? 1))
        setDebugExecTimeout(String(cfg.debug_exec_timeout ?? 60))
        setDebugMaxRounds(String(cfg.debug_max_rounds ?? 10))
        setGenModel(cfg.gen?.model ?? '')
        setGenApiKey('')
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const handleSave = async () => {
    if (!model) { setSaveError('请选择大模型类型'); return }
    // 未输入新 key 且原本也没有 key 时，必须输入 key
    if (!apiKey.trim() && !config?.has_api_key) { setSaveError('请输入 API Key'); return }
    setSaving(true)
    setSaved(false)
    setSaveError('')
    try {
      // api_key 为空表示保留现有 key（后端会跳过 key 更新）
      await configApi.updateLLM({
        model,
        api_key: apiKey.trim(),
        timeout: Number(llmTimeout) || undefined,
        max_concurrency: Number(maxConcurrency) || undefined,
        debug_exec_timeout: Number(debugExecTimeout) || undefined,
        debug_max_rounds: Number(debugMaxRounds) || undefined,
        gen_model: genModel,
        gen_api_key: genApiKey.trim(),
      })
      setSaved(true)
      setConfig((prev) => prev ? { ...prev, model, has_api_key: true } : prev)
      setGenApiKey('')
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const supportedModels: SupportedModel[] = config?.supported_models ?? [
    { value: 'deepseek:deepseek-v4-pro', label: 'DeepSeek · deepseek-v4-pro', provider: 'deepseek' },
  ]
  // 生成模型当前值不在预设列表里（如自建网关的手工模型）时补一个选项，避免下拉回显为空
  const genModels: SupportedModel[] = (() => {
    const cur = config?.gen?.model
    if (!cur || supportedModels.some((m) => m.value === cur)) return supportedModels
    return [
      { value: cur, label: `${config?.gen?.provider ?? ''} · ${config?.gen?.model_name ?? cur}（当前）`, provider: config?.gen?.provider ?? '' },
      ...supportedModels,
    ]
  })()

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-2xl mx-auto px-6 py-6">
        {/* 标题 */}
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-maia-accent/10 border border-maia-accent-border">
            <Settings className="h-4.5 w-4.5 text-maia-accent" />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-maia-text-heading">系统设置</h1>
            <p className="text-[12px] text-maia-text-muted">配置系统背后所使用的大模型</p>
          </div>
        </div>

        {loading && (
          <div className="flex items-center gap-2 px-2 py-8 text-sm text-maia-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />加载配置中...
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-3 py-2.5 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[13px]">
            <AlertCircle className="h-4 w-4" />加载配置失败：{error}
          </div>
        )}

        {!loading && !error && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-maia-accent" />
                <span className="text-[13px] font-semibold text-maia-text-heading">大模型配置</span>
              </div>
            </CardHeader>
            <CardBody className="space-y-5">
              {/* 模型类型下拉框 */}
              <div>
                <label className="block text-[13px] text-maia-text-secondary mb-1.5">对话模型（需支持工具调用）</label>
                <div className="relative">
                  <select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="h-9 w-full appearance-none rounded border border-maia-border bg-maia-surface px-3 pr-9 text-[13px] text-maia-text focus:outline-none focus:ring-2 focus:ring-maia-accent/20 focus:border-maia-accent cursor-pointer"
                  >
                    {supportedModels.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-maia-text-muted text-[13px]">▾</span>
                </div>
                <p className="text-[12px] text-maia-text-muted mt-1">
                  环境变量：<span className="font-mono">LLM_PROVIDER</span> / <span className="font-mono">LLM_MODEL</span>
                </p>
              </div>

              {/* API Key */}
              <div>
                <label className="block text-[13px] text-maia-text-secondary mb-1.5">API Key</label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <KeyRound className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-maia-text-muted" />
                    <Input
                      type={showKey ? 'text' : 'password'}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={config?.has_api_key ? '已配置（留空则保留原值，输入新值以替换）' : '请输入当前服务商的 API Key'}
                      className="pl-8 pr-9 font-mono"
                    />
                    <button
                      type="button"
                      onClick={() => setShowKey((v) => !v)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-maia-text-muted hover:text-maia-text"
                      title={showKey ? '隐藏' : '显示'}
                    >
                      {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-1.5">
                  {config?.has_api_key && <Badge variant="accent" className="text-[12px] shrink-0">已配置</Badge>}
                  <p className="text-[12px] text-maia-text-muted">
                    环境变量：<span className="font-mono">LLM_API_KEY</span>
                  </p>
                </div>
              </div>

              {/* 生成/推理增强模型（可选） */}
              <div className="pt-4 border-t border-maia-border">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[13px] font-medium text-maia-text-secondary">生成 / 推理增强模型</span>
                  {config?.gen?.enabled
                    ? <Badge variant="accent" className="text-[12px] shrink-0">已启用</Badge>
                    : <Badge variant="default" className="text-[12px] shrink-0">跟随对话模型</Badge>}
                </div>
                <p className="text-[12px] text-maia-text-muted mb-3">
                  仅用于生成工具/Agent 的规范与代码，不参与对话，因此<b className="text-maia-text-secondary">无需支持工具调用</b>，
                  可以选写代码与逻辑推理更强的模型。留空即跟随上方对话模型。
                </p>
                <div className="relative">
                  <select
                    value={genModel}
                    onChange={(e) => setGenModel(e.target.value)}
                    className="h-9 w-full appearance-none rounded border border-maia-border bg-maia-surface px-3 pr-9 text-[13px] text-maia-text focus:outline-none focus:ring-2 focus:ring-maia-accent/20 focus:border-maia-accent cursor-pointer"
                  >
                    <option value="">跟随对话模型（默认）</option>
                    {genModels.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-maia-text-muted text-[13px]">▾</span>
                </div>
                {genModel && (
                  <div className="mt-2 relative">
                    <KeyRound className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-maia-text-muted" />
                    <Input
                      type={showGenKey ? 'text' : 'password'}
                      value={genApiKey}
                      onChange={(e) => setGenApiKey(e.target.value)}
                      placeholder={config?.gen?.has_api_key
                        ? '生成模型 Key 已配置（留空则沿用该服务商已有 Key，输入新值以替换）'
                        : '生成模型 API Key（留空则沿用该服务商已有 Key）'}
                      className="pl-8 pr-9 font-mono"
                    />
                    <button
                      type="button"
                      onClick={() => setShowGenKey((v) => !v)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-maia-text-muted hover:text-maia-text"
                      title={showGenKey ? '隐藏' : '显示'}
                    >
                      {showGenKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                )}
                <p className="text-[11px] text-maia-text-muted mt-1.5">
                  环境变量：<span className="font-mono">LLM_GEN_PROVIDER</span> /{' '}
                  <span className="font-mono">LLM_GEN_MODEL</span> /{' '}
                  <span className="font-mono">LLM_GEN_API_KEY</span>
                </p>
              </div>

              {/* 运行参数：超时 / 并发 / 调试 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[13px] font-medium text-maia-text-secondary">运行参数</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[12px] text-maia-text-muted mb-1">LLM 请求超时（秒）</label>
                    <Input
                      type="number" min={30} max={7200}
                      value={llmTimeout}
                      onChange={(e) => setLlmTimeout(e.target.value)}
                      className="font-mono"
                    />
                    <p className="text-[11px] text-maia-text-muted mt-1">
                      长文本生成建议 ≥1200；环境变量 <span className="font-mono">LLM_TIMEOUT</span>
                    </p>
                  </div>
                  <div>
                    <label className="block text-[12px] text-maia-text-muted mb-1">LLM 并发上限</label>
                    <Input
                      type="number" min={1} max={16}
                      value={maxConcurrency}
                      onChange={(e) => setMaxConcurrency(e.target.value)}
                      className="font-mono"
                    />
                    <p className="text-[11px] text-maia-text-muted mt-1">
                      网关仅支持串行时保持 1；环境变量 <span className="font-mono">LLM_MAX_CONCURRENCY</span>
                    </p>
                  </div>
                  <div>
                    <label className="block text-[12px] text-maia-text-muted mb-1">调试单轮执行超时（秒）</label>
                    <Input
                      type="number" min={5} max={1800}
                      value={debugExecTimeout}
                      onChange={(e) => setDebugExecTimeout(e.target.value)}
                      className="font-mono"
                    />
                    <p className="text-[11px] text-maia-text-muted mt-1">
                      防工具死循环挂起；环境变量 <span className="font-mono">DEBUG_EXEC_TIMEOUT</span>
                    </p>
                  </div>
                  <div>
                    <label className="block text-[12px] text-maia-text-muted mb-1">调试最大轮数</label>
                    <Input
                      type="number" min={1} max={100}
                      value={debugMaxRounds}
                      onChange={(e) => setDebugMaxRounds(e.target.value)}
                      className="font-mono"
                    />
                    <p className="text-[11px] text-maia-text-muted mt-1">
                      超过即停止；环境变量 <span className="font-mono">DEBUG_MAX_ROUNDS</span>
                    </p>
                  </div>
                </div>
              </div>

              {/* 保存结果反馈 */}
              {saveError && (
                <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-[13px]">
                  <AlertCircle className="h-4 w-4" />{saveError}
                </div>
              )}
              {saved && (
                <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-success/30 bg-maia-success/10 text-maia-success text-[13px]">
                  <CheckCircle2 className="h-4 w-4" />配置已保存并写入 .env 文件，已即时生效
                </div>
              )}

              <div className="flex items-center justify-between gap-4 pt-4 mt-1 border-t border-maia-border">
                <div className="min-w-0 text-[12px] text-maia-text-muted">
                  <div className="truncate">
                    Provider：<span className="font-mono text-maia-text-secondary">{config?.provider ?? 'deepseek'}</span>
                  </div>
                  <div className="truncate">
                    Base URL：<span className="font-mono text-maia-text-secondary">{config?.base_url ?? '-'}</span>
                  </div>
                </div>
                <Button onClick={handleSave} disabled={saving} className="shrink-0 whitespace-nowrap">
                  {saving ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />保存中…</> : <>保存配置</>}
                </Button>
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}
