import { useResourceStore } from '@/stores/resource-store'
import { useFileTreeStore } from '@/stores/file-tree-store'
import { ResourceProperties } from './ResourceProperties'
import { Info } from 'lucide-react'

export function RightPanel() {
  const selectedResource = useResourceStore((s) => s.selectedResource)
  const selectedFile = useFileTreeStore((s) => s.selectedFile)

  // Priority: selected resource > selected file task status
  if (selectedResource) {
    return <ResourceProperties resource={selectedResource} />
  }

  if (selectedFile) {
    return (
      <div className="flex flex-col h-full">
        <div className="px-3 py-2 border-b border-maia-border bg-maia-bg">
          <h3 className="text-xs font-semibold text-maia-text">文件属性</h3>
        </div>
        <div className="p-3 space-y-3">
          <div>
            <span className="text-[10px] text-maia-text-muted uppercase">名称</span>
            <p className="text-sm font-medium">{selectedFile.name}</p>
          </div>
          <div>
            <span className="text-[10px] text-maia-text-muted uppercase">类型</span>
            <p className="text-sm">{selectedFile.type === 'directory' ? '文件夹' : selectedFile.format?.toUpperCase()}</p>
          </div>
          <div>
            <span className="text-[10px] text-maia-text-muted uppercase">路径</span>
            <p className="text-xs text-maia-text-secondary font-mono">{selectedFile.path}</p>
          </div>
          {selectedFile.size && (
            <div>
              <span className="text-[10px] text-maia-text-muted uppercase">大小</span>
              <p className="text-sm">{selectedFile.size > 1048576 ? `${(selectedFile.size / 1048576).toFixed(1)} MB` : `${(selectedFile.size / 1024).toFixed(1)} KB`}</p>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full items-center justify-center text-center p-6">
      <Info className="h-8 w-8 text-maia-text-muted mb-3" />
      <p className="text-sm text-maia-text-muted">
        选择文件或资源
        <br />
        查看属性详情
      </p>
    </div>
  )
}
