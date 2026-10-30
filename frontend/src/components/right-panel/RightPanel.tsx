import { useResourceStore } from '@/stores/resource-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { ResourceProperties } from './ResourceProperties'
import { ChatContextPanel } from './ChatContextPanel'

export function RightPanel() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const selectedFile = useFileTreeStore((s) => s.selectedFile)

  // Priority: selected resource > selected file > 会话上下文
  if (selectedResource) {
    return <ResourceProperties resource={selectedResource} />
  }

  if (selectedFile) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center h-8 px-3 shrink-0 border-b border-maia-border text-[13px] text-maia-text-muted">
          文件属性
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

  return <ChatContextPanel />
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
