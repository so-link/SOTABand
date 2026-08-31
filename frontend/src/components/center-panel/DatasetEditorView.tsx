import { useRef } from 'react'
import { ArrowRight, ArrowLeft, CheckCircle2, XCircle, Loader2, Rocket, Database, File, Upload, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardBody } from '@/components/ui/card'
import { useUIStore } from '@/stores/ui-store'
import { useDatasetEditorStore } from '@/stores/dataset-editor-store'

export function DatasetEditorView() {
  const store = useDatasetEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)

  return (
    <div className="flex flex-col h-full bg-maia-surface">
      <div className="flex items-center justify-between px-4 py-2 border-b border-maia-border bg-maia-bg/50 shrink-0">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-blue-500" />
          <span className="text-sm font-semibold text-maia-text-heading tracking-wide">数据集编辑器</span>
        </div>
        <button onClick={() => { store.reset(); setActiveView('chat') }} className="text-maia-text-muted hover:text-maia-text text-sm">× 关闭</button>
      </div>

      <div className="flex items-center gap-0 px-4 py-2 border-b border-maia-border bg-maia-surface shrink-0">
        {[1, 2, 3].map((s, i) => (
          <div key={s} className="flex items-center gap-0">
            <div className={`flex items-center gap-1.5 text-[11px] font-medium tracking-wide px-2 py-1 rounded-full ${
              store.step === s ? 'bg-maia-accent text-white' : store.step > s ? 'bg-maia-success/10 text-maia-success' : 'text-maia-text-muted'
            }`}>
              {store.step > s ? <CheckCircle2 className="h-3 w-3" /> : <span className="text-[10px]">{s}</span>}
              {['上传', '审阅', '注册'][i]}
            </div>
            {i < 2 && <div className="w-6 h-[1px] bg-maia-border mx-1" />}
          </div>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-4">
        {store.step === 1 && <Step1 />}
        {store.step === 2 && <Step2 />}
        {store.step === 3 && <Step3 />}
      </div>
    </div>
  )
}

function Step1() {
  const { files, description, setDescription, uploadFile, removeFile, setFileDescription, generateSpec, isGenerating, error } = useDatasetEditorStore()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    if (!fileList?.length) return
    for (const f of Array.from(fileList)) {
      await uploadFile(f)
    }
    e.target.value = ''
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 1: 上传文件 & 描述数据集</h3>

      {/* Upload area */}
      <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleUpload} />
      <div
        onClick={() => fileInputRef.current?.click()}
        className="border-2 border-dashed border-maia-border rounded-lg p-6 text-center cursor-pointer hover:border-maia-accent/40 hover:bg-maia-accent-light/20 transition-colors mb-4"
      >
        <Upload className="h-6 w-6 text-maia-text-muted mx-auto mb-1" />
        <p className="text-sm text-maia-text-secondary">点击上传文件（支持多选）</p>
        <p className="text-[10px] text-maia-text-muted mt-1">图片、表格、文本、EDF 等格式</p>
      </div>

      {/* Uploaded files with descriptions */}
      {files.length > 0 && (
        <div className="mb-4 space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-medium text-maia-text-secondary"><File className="h-3 w-3" />已上传 {files.length} 个文件</div>
          {files.map(f => (
            <Card key={f.id} className="border-maia-border">
              <CardBody>
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="text-xs font-medium text-maia-text truncate">{f.fileName}</span>
                      <Badge variant="default" className="text-[9px]">{f.format.toUpperCase()}</Badge>
                      <span className="text-[10px] text-maia-text-muted">{f.fileSize > 1024 ? `${(f.fileSize / 1024).toFixed(0)}KB` : `${f.fileSize}B`}</span>
                    </div>
                    <input
                      type="text"
                      value={f.description}
                      onChange={(e) => setFileDescription(f.id, e.target.value)}
                      placeholder="描述此文件的内容（如: 64通道EEG静息态数据，5分钟时长）"
                      className="w-full text-[11px] border border-maia-border rounded px-2 py-1 outline-none focus:border-maia-accent/40"
                    />
                  </div>
                  <button onClick={() => removeFile(f.id)} className="text-maia-text-muted hover:text-maia-danger shrink-0 mt-1"><X className="h-3.5 w-3.5" /></button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {/* Overall description */}
      <div className="mb-4">
        <label className="text-xs font-medium text-maia-text-secondary block mb-1">数据集整体描述</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="描述这个数据集的整体情况、用途和背景（如: '64通道EEG脑电数据，采样率256Hz，包含静息态和视觉刺激态两段记录，适用于脑电信号分析研究'）"
          rows={3}
          className="w-full rounded-lg border border-maia-border bg-maia-surface px-3 py-2 text-[13px] outline-none resize-none"
        />
      </div>

      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}

      <div className="flex justify-end mt-4">
        <Button onClick={generateSpec} disabled={(!description.trim() && files.length === 0) || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />生成中...</> : <>生成 MD 文档<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step2() {
  const { generatedMd, setGeneratedMd, register, setStep, isGenerating, error } = useDatasetEditorStore()
  return (
    <div className="max-w-3xl mx-auto">
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">Step 2: 审阅 MD 规范文档</h3>
      <textarea value={generatedMd} onChange={(e) => setGeneratedMd(e.target.value)} rows={18}
        className="w-full rounded-lg border border-maia-border bg-maia-bg/50 px-4 py-3 text-[12px] font-mono outline-none resize-y" spellCheck={false} />
      {error && <div className="flex items-center gap-1.5 mt-2 text-xs text-maia-danger"><XCircle className="h-3 w-3" />{error}</div>}
      <div className="flex justify-between mt-4">
        <Button variant="outline" onClick={() => setStep(1)}><ArrowLeft className="h-3.5 w-3.5" />返回</Button>
        <Button onClick={register} disabled={!generatedMd.trim() || isGenerating}>
          {isGenerating ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />注册中...</> : <>注册数据集<ArrowRight className="h-3.5 w-3.5" /></>}
        </Button>
      </div>
    </div>
  )
}

function Step3() {
  const { registeredId, reset } = useDatasetEditorStore()
  const setActiveView = useUIStore((s) => s.setActiveView)
  return (
    <div className="max-w-md mx-auto text-center py-12">
      <div className="flex justify-center mb-4"><div className="flex items-center justify-center h-16 w-16 rounded-full bg-maia-success/10"><Rocket className="h-8 w-8 text-maia-success" /></div></div>
      <h3 className="text-lg font-semibold text-maia-text-heading mb-2 tracking-wide">数据集已注册！</h3>
      <Card className="border-maia-border mt-4"><CardBody><div className="space-y-2 text-left"><div className="flex justify-between text-xs"><span className="text-maia-text-muted">Dataset ID</span><span className="font-mono text-maia-text">{registeredId}</span></div><div className="flex justify-between text-xs"><span className="text-maia-text-muted">状态</span><Badge variant="success">已注册</Badge></div></div></CardBody></Card>
      <div className="flex gap-3 justify-center mt-6">
        <Button variant="outline" onClick={() => { reset(); setActiveView('chat') }}>返回对话</Button>
        <Button onClick={() => reset()}>创建新数据集</Button>
      </div>
    </div>
  )
}
