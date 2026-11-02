import { useState, useEffect } from 'react'
import { FileText } from 'lucide-react'
import { useResourceStore } from '@/stores/resource-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import {
  useUIStore,
  parseAgentDetailView,
  parseToolDetailView,
  parseModelDetailView,
} from '@/stores/ui-store'
import { ResourceProperties } from './ResourceProperties'
import { ChatHistoryPanel } from './ChatHistoryPanel'
import { DatasetFilesPanel } from './DatasetFilesPanel'

/**
 * 右栏详情：内容与中栏当前激活的标签一一对应（切换标签即切换右栏）。
 * - 对话标签 → 对话历史（可点回历史会话继续对话）
 * - Agent/工具/模型详情标签 → 对应资源的详情属性
 * - 数据预览标签 → tab 切换「数据集文件 / 资源详情」
 * - 文件预览标签 → 当前文件属性
 * 详情不是可关闭的独立面板，只随标签切换。
 */
export function RightPanel() {
  const activeView = useUIStore((s) => s.activeView)
  const agentResources = useResourceStore((s) => s.agentResources)
  const toolResources = useResourceStore((s) => s.toolResources)
  const modelResources = useResourceStore((s) => s.modelResources)
  const cachedDatasetForDetail = useResourceStore((s) => s.cachedDatasetForDetail)
  const previewFiles = useResourceStore((s) => s.previewFiles)
  const selectedFile = useFileTreeStore((s) => s.selectedFile)
  // 数据预览标签的右栏 tab：数据集文件 / 资源详情（Hook 须在任何 early return 之前调用）
  const [dataTab, setDataTab] = useState<'files' | 'detail'>('files')
  const datasetId = cachedDatasetForDetail?.id ?? null
  useEffect(() => { setDataTab('files') }, [datasetId])

  if (activeView === 'chat') {
    return <ChatHistoryPanel />
  }

  const agentId = parseAgentDetailView(activeView)
  if (agentId) {
    const res = agentResources.find((r) => r.id === agentId)
    return res ? <ResourceProperties resource={res} /> : <EmptyDetail label="Agent" />
  }

  const toolId = parseToolDetailView(activeView)
  if (toolId) {
    const res = toolResources.find((r) => r.id === toolId)
    return res ? <ResourceProperties resource={res} /> : <EmptyDetail label="工具" />
  }

  const modelId = parseModelDetailView(activeView)
  if (modelId) {
    const res = modelResources.find((r) => r.id === modelId)
    return res ? <ResourceProperties resource={res} /> : <EmptyDetail label="模型" />
  }

  if (activeView === 'data-preview') {
    if (!cachedDatasetForDetail) return <EmptyDetail label="数据集" />
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-stretch h-9 shrink-0 border-b border-maia-border bg-maia-bg">
          <TabButton active={dataTab === 'files'} onClick={() => setDataTab('files')}>
            数据集文件
            {previewFiles.length > 0 && (
              <span className="text-[11px] text-maia-text-muted">{previewFiles.length}</span>
            )}
          </TabButton>
          <TabButton active={dataTab === 'detail'} onClick={() => setDataTab('detail')}>
            资源详情
          </TabButton>
        </div>
        <div className="flex-1 min-h-0">
          {dataTab === 'files'
            ? <DatasetFilesPanel hideHeader />
            : <ResourceProperties resource={cachedDatasetForDetail} hideHeader />}
        </div>
      </div>
    )
  }

  if (activeView === 'file-preview') {
    return selectedFile ? <FileProperties /> : <EmptyDetail label="文件" />
  }

  return <EmptyDetail label="当前标签" />
}

function TabButton({ active, onClick, children }: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-3 text-[13px] transition-colors border-b-2 ${
        active
          ? 'text-maia-text font-medium border-maia-accent'
          : 'text-maia-text-muted border-transparent hover:text-maia-text-secondary'
      }`}
    >
      {children}
    </button>
  )
}

function FileProperties() {
  const selectedFile = useFileTreeStore((s) => s.selectedFile)
  if (!selectedFile) return null
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center h-9 px-3 shrink-0 border-b border-maia-border">
        <span className="text-[13px] text-maia-text-muted">文件属性</span>
      </div>
      <div className="p-3 flex flex-col gap-3">
        <Prop label="名称" value={selectedFile.name} />
        <Prop label="类型" value={selectedFile.type === 'directory' ? '文件夹' : (selectedFile.format?.toUpperCase() ?? '文件')} />
        <Prop label="路径" value={selectedFile.path} mono />
        {selectedFile.size !== undefined && (
          <Prop
            label="大小"
            value={selectedFile.size > 1048576
              ? `${(selectedFile.size / 1048576).toFixed(1)} MB`
              : `${(selectedFile.size / 1024).toFixed(1)} KB`}
          />
        )}
      </div>
    </div>
  )
}

function EmptyDetail({ label }: { label: string }) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center h-9 px-3 shrink-0 border-b border-maia-border">
        <span className="text-[13px] text-maia-text-muted">资源详情</span>
      </div>
      <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
        <FileText className="h-5 w-5 text-maia-text-muted" />
        <p className="text-[13px] text-maia-text-muted">当前「{label}」没有可显示的详情</p>
      </div>
    </div>
  )
}

function Prop({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[13px] text-maia-text-muted">{label}</span>
      <span className={mono ? 'text-[13px] text-maia-text-secondary font-mono break-all' : 'text-[13px] text-maia-text'}>
        {value}
      </span>
    </div>
  )
}
