import { useState, useEffect } from 'react'
import {

  Wrench, ArrowRight, ArrowLeft, CheckCircle2, XCircle,
  Loader2, FileCode, Play, CheckCheck, Rocket,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useToolEditorStore } from '@/stores/tool-editor-store'

export function ToolEditorView() {
  const store = useToolEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Wrench className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">工具编辑器</span>
        </div>
        <button onClick={() => { store.reset(); setActiveView('chat') }} className="text-maia-text-muted hover:text-maia-text text-sm">× 关闭</button>
      </div>

      {/* Step indicators */}
      <div className="flex items-center gap-0 px-4 py-2 border-b border-maia-border bg-white shrink-0">
        {[1, 2, 3, 4].map((s, i) => (
          <div key={s} className="flex items-center gap-0">
            <div className={`flex items-center gap-1.5 text-[11px] font-medium tracking-wide px-2 py-1 rounded-full transition-colors ${
              store.step === s ? 'bg-maia-accent text-white' : store.step > s ? 'bg-maia-success/10 text-maia-success' : 'text-maia-text-muted'
            }`}>
              {store.step > s ? <CheckCircle2 className="h-3 w-3" /> : <span className="text-[10px]">{s}</span>}
              {['描述', '审阅', '核验', '注册'][i]}
            </div>
            {i < 3 && <div className="w-6 h-[1px] bg-maia-border mx-1" />}
          </div>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-4">
        {store.step === 1 && <Step1 />}
        {store.step === 2 && <Step2 />}
        {store.step === 3 && <Step3 />}
        {store.step === 4 && <Step4 />}
      </div>
    </div>
  )
}

function Step1() {
  const { description, setDescription, generateSpec, isGenerating, error } = useToolEditorStore()
  return (
    <div className="max-w-2xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 1: 描述工具需求</h3>
      <p className="text-sm text-maia-text-secondary mb-4">用自然语言描述你需要什么工具，系统会调用大模型生成标准化的 MD 工具描述文档。</p>
      <textarea value={description} onChange={(e) => setDescription(e.target.value)}
        placeholder='例如: "我需要一个EEG带通滤波器，支持delta/theta/alpha/beta/gamma频段，Butterworth滤波器，输入EDF文件，输出滤波后的EDF文件"'
        rows={6} className="w-full rounded-lg border border-maia-border bg-white px-4 py-3 text-[13px] tracking-wide outline-none resize-none focus:border-maia-accent/40 placeholder:text-maia-text-muted" />
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-end mt-4">
        <Button onClick={generateSpec} disabled={!description.trim() || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />生成中...</> : <>生成 MD 文档<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step2() {
  const { generatedMd, setGeneratedMd, generateCode, setStep, isGenerating, error } = useToolEditorStore()
  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 2: 审阅 & 编辑 MD 规范文档</h3>
      <p className="text-sm text-maia-text-secondary mb-4">以下是 AI 生成的工具规范文档，你可以直接编辑修改。</p>
      <textarea value={generatedMd} onChange={(e) => setGeneratedMd(e.target.value)} rows={18}
        className="w-full rounded-lg border border-maia-border bg-maia-bg/50 px-4 py-3 text-[12px] font-mono outline-none resize-y focus:border-maia-accent/40" spellCheck={false} />
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(1)}><ArrowLeft className="h-3.5 w-3.5" />返回修改需求</Button>
        <Button onClick={generateCode} disabled={!generatedMd.trim() || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />生成中...</> : <>生成代码和测试<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step3() {
  const { generatedCode, sandboxResults, testData, registerTool, runTests, setStep, isGenerating, isTesting, error } = useToolEditorStore()
  const passed = (sandboxResults as Record<string, unknown>)?.passed as string[] || []
  const failed = (sandboxResults as Record<string, unknown>)?.failed as string[] || []
  const tested = passed.length > 0 || failed.length > 0
  const [testInputs, setTestInputs] = useState<Record<string, string>>({})

  // 初始化测试输入（从 testData 中提取）
  useEffect(() => {
    if (testData) {
      const normal = (testData as Record<string, unknown>).normal as Record<string, unknown> | undefined
      if (normal?.input) {
        const input = normal.input as Record<string, unknown>
        const init: Record<string, string> = {}
        Object.entries(input).forEach(([k, v]) => { init[k] = String(v ?? '') })
        setTestInputs(init)
      }
    }
  }, [testData])

  return (
    <div className="max-w-4xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 3: 代码预览 & 沙箱测试</h3>
      <div className="grid grid-cols-5 gap-4">
        <div className="col-span-3">
          <div className="flex items-center gap-1.5 mb-2"><FileCode className="h-3.5 w-3.5 text-amber-500" /><span className="text-xs font-medium text-maia-text-secondary tracking-wide">生成代码</span></div>
          <pre className="rounded-lg border border-maia-border bg-maia-bg/50 px-3 py-2 text-[11px] font-mono leading-relaxed overflow-auto max-h-[300px] whitespace-pre-wrap">{generatedCode}</pre>
        </div>
        <div className="col-span-2">
          <div className="flex items-center gap-1.5 mb-2"><Play className="h-3.5 w-3.5 text-amber-500" /><span className="text-xs font-medium text-maia-text-secondary tracking-wide">沙箱测试</span></div>

          {/* Editable test inputs */}
          {Object.keys(testInputs).length > 0 && (
            <div className="mb-2 space-y-1.5">
              <div className="text-[10px] text-maia-text-muted uppercase tracking-wider">测试输入</div>
              {Object.entries(testInputs).map(([key, val]) => (
                <input key={key} type="text" value={val}
                  onChange={e => setTestInputs(p => ({...p, [key]: e.target.value}))}
                  className="w-full h-7 rounded border border-maia-border px-2 text-[11px] font-mono outline-none focus:border-maia-accent/40"
                  placeholder={key} />
              ))}
            </div>
          )}

          <Card className="border-maia-border">
            <CardBody>
              <div className="space-y-1.5">
                {passed.map((msg, i) => <div key={i} className="flex items-center gap-1.5 text-xs text-maia-success"><CheckCheck className="h-3 w-3 shrink-0" />{msg}</div>)}
                {failed.map((msg, i) => <div key={i} className="flex items-center gap-1.5 text-xs text-maia-danger"><XCircle className="h-3 w-3 shrink-0" />{msg}</div>)}
                {!tested && !isTesting && <div className="text-xs text-maia-text-muted">点击下方按钮运行测试</div>}
                {isTesting && <div className="flex items-center gap-1.5 text-xs text-maia-accent"><Loader2 className="h-3 w-3 animate-spin" />测试中...</div>}
              </div>
            </CardBody>
          </Card>
          <Button variant="outline" size="sm" className="mt-2 w-full" onClick={runTests} disabled={isTesting || !generatedCode}>
            {isTesting ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />测试中...</> : <><Play className="h-3.5 w-3.5" />运行沙箱测试</>}
          </Button>
          {/* 测试输入/输出详情 */}
          {tested && sandboxResults && (
            <TestDetail detail={(sandboxResults as Record<string, unknown>).test_details as Record<string, unknown> | undefined} />
          )}
        </div>
      </div>
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(2)}><ArrowLeft className="h-3.5 w-3.5" />返回修改 MD</Button>
        <div className="flex gap-2">
          <Button variant="danger" onClick={() => setStep(1)}><XCircle className="h-3.5 w-3.5" />拒绝</Button>
          <Button onClick={registerTool} disabled={isGenerating || failed.length > 0 || !tested}>
            {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />注册中...</> : <><CheckCircle2 className="h-3.5 w-3.5" />批准并注册发布</>}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Step4() {
  const { registeredId, reset } = useToolEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)
  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="flex justify-center mb-4"><div className="flex items-center justify-center h-16 w-16 rounded-full bg-maia-success/10"><Rocket className="h-8 w-8 text-maia-success" /></div></div>
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">工具已发布！</h3>
      <Card className="border-maia-border mt-4"><CardBody><div className="space-y-2 text-left"><div className="flex justify-between text-xs"><span className="text-maia-text-muted">Tool ID</span><span className="font-mono text-maia-text">{registeredId}</span></div><div className="flex justify-between text-xs"><span className="text-maia-text-muted">状态</span><Badge variant="success">已注册</Badge></div></div></CardBody></Card>
      <div className="flex gap-3 justify-center mt-6">
        <Button variant="outline" onClick={() => { reset(); setActiveView('chat') }}>返回对话</Button>
        <Button onClick={() => reset()}><Wrench className="h-3.5 w-3.5" />创建新工具</Button>
      </div>
    </div>
  )
}

// ─ 测试输入/输出详情 ─

function TestDetail({ detail }: { detail?: Record<string, unknown> }) {
  if (!detail) return null
  const input = detail.input as Record<string, unknown> | undefined
  const output = detail.output as Record<string, unknown> | undefined
  const error = detail.error as string | undefined

  return (
    <div className="mt-3 space-y-2">
      {input && (
        <div>
          <div className="text-[10px] text-maia-text-muted mb-0.5 uppercase tracking-wider">测试输入</div>
          <pre className="rounded border border-maia-border bg-maia-bg px-2 py-1.5 text-[10px] font-mono leading-relaxed max-h-[80px] overflow-auto whitespace-pre-wrap">
            {JSON.stringify(input, null, 2)}
          </pre>
        </div>
      )}
      {output && (
        <div>
          <div className="text-[10px] text-maia-success mb-0.5 uppercase tracking-wider">执行输出</div>
          <pre className="rounded border border-emerald-200 bg-emerald-50/50 px-2 py-1.5 text-[10px] font-mono leading-relaxed max-h-[120px] overflow-auto whitespace-pre-wrap">
            {typeof output === 'string' ? output : JSON.stringify(output, null, 2)}
          </pre>
        </div>
      )}
      {error && (
        <div>
          <div className="text-[10px] text-maia-danger mb-0.5 uppercase tracking-wider">错误</div>
          <pre className="rounded border border-red-200 bg-red-50/50 px-2 py-1.5 text-[10px] font-mono max-h-[80px] overflow-auto whitespace-pre-wrap">{error}</pre>
        </div>
      )}
    </div>
  )
}
