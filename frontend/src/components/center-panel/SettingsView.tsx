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

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const cfg = await configApi.getLLM()
        if (cancelled) return
        setConfig(cfg)
        setModel(cfg.model)
        setApiKey(cfg.api_key) // 脱敏后的 key，作为占位
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
    if (!apiKey.trim() && !config?.has_api_key) { setSaveError('请输入 API Key'); return }
    setSaving(true)
    setSaved(false)
    setSaveError('')
    try {
      await configApi.updateLLM({ model, api_key: apiKey })
      setSaved(true)
      // 更新本地配置状态
      setConfig((prev) => prev ? { ...prev, model, has_api_key: true } : prev)
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const supportedModels: SupportedModel[] = config?.supported_models ?? [
    { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro', provider: 'deepseek' },
  ]

  return (
    <div className="h-full overflow-auto">
      <div className="max-w-2xl mx-auto px-6 py-6">
        {/* 标题 */}
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-maia-accent/10 border border-maia-accent-border">
            <Settings className="h-4.5 w-4.5 text-maia-accent" />
          </div>
          <div>
            <h1 className="text-[15px] font-semibold text-maia-text-heading tracking-wider">系统设置</h1>
            <p className="text-[11px] text-maia-text-muted tracking-wide">配置系统背后所使用的大模型</p>
          </div>
        </div>

        {loading && (
          <div className="flex items-center gap-2 px-2 py-8 text-sm text-maia-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />加载配置中...
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-3 py-2.5 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-xs">
            <AlertCircle className="h-4 w-4" />加载配置失败：{error}
          </div>
        )}

        {!loading && !error && (
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-maia-accent" />
                <span className="text-[13px] font-semibold text-maia-text-heading tracking-wide">大模型配置</span>
              </div>
            </CardHeader>
            <CardBody className="space-y-5">
              {/* 模型类型下拉框 */}
              <div>
                <label className="block text-[12px] text-maia-text-secondary mb-1.5 tracking-wide">大模型类型</label>
                <div className="relative">
                  <select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="h-9 w-full appearance-none rounded border border-maia-border bg-maia-surface px-3 pr-9 text-[12px] text-maia-text tracking-wide focus:outline-none focus:ring-2 focus:ring-maia-accent/20 focus:border-maia-accent cursor-pointer"
                  >
                    {supportedModels.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </select>
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-maia-text-muted text-xs">▾</span>
                </div>
                <p className="text-[10px] text-maia-text-muted mt-1 tracking-wide">
                  环境变量：<span className="font-mono">DEEPSEEK_MODEL</span>
                </p>
              </div>

              {/* API Key */}
              <div>
                <label className="block text-[12px] text-maia-text-secondary mb-1.5 tracking-wide">API Key</label>
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <KeyRound className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-maia-text-muted" />
                    <Input
                      type={showKey ? 'text' : 'password'}
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                      placeholder={config?.has_api_key ? '已配置（输入新值以替换）' : '请输入 DeepSeek API Key'}
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
                <div className="flex items-center gap-1.5 mt-1">
                  {config?.has_api_key && <Badge variant="accent" className="text-[10px]">已配置</Badge>}
                  <p className="text-[10px] text-maia-text-muted tracking-wide">
                    环境变量：<span className="font-mono">DEEPSEEK_API_KEY</span>
                  </p>
                </div>
              </div>

              {/* 保存结果反馈 */}
              {saveError && (
                <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-danger/30 bg-maia-danger/10 text-maia-danger text-xs">
                  <AlertCircle className="h-4 w-4" />{saveError}
                </div>
              )}
              {saved && (
                <div className="flex items-center gap-2 px-3 py-2 rounded border border-maia-success/30 bg-maia-success/10 text-maia-success text-xs">
                  <CheckCircle2 className="h-4 w-4" />配置已保存并写入 .env 文件，已即时生效
                </div>
              )}

              <div className="flex items-center justify-between pt-1">
                <div className="text-[10px] text-maia-text-muted tracking-wide">
                  当前 Provider：<span className="font-mono text-maia-text-secondary">{config?.provider ?? 'deepseek'}</span>
                  {' · '}Base URL：<span className="font-mono text-maia-text-secondary">{config?.base_url ?? '-'}</span>
                </div>
                <Button onClick={handleSave} disabled={saving}>
                  {saving ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />保存中...</> : <>保存配置</>}
                </Button>
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}
