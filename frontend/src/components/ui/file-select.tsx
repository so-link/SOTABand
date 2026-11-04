import { useState, useEffect } from 'react'

interface FileOption {
  file_id: string
  name: string
}

/** 文件下拉选择器：选项来自「我的文件」（/api/file），选中值为 file_id。
 *
 *  工具/Agent 的参数以前用 data_path（绝对路径），浏览器拿到路径等于拿到
 *  越权素材；现在统一用 file_id——执行前由平台解析回真实路径。 */
export function FileSelect({
  value,
  onChange,
  placeholder = '选择文件…',
  className,
}: {
  value: string
  onChange: (fileId: string) => void
  placeholder?: string
  className?: string
}) {
  const [files, setFiles] = useState<FileOption[]>([])

  useEffect(() => {
    fetch('/api/file')
      .then(r => r.json())
      .then(d => setFiles(d.files || []))
      .catch(() => { /* 文件空间不可达时退化为空列表 */ })
  }, [])

  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className={className || 'h-8 w-full rounded border border-maia-border bg-maia-surface px-2 text-[13px] text-maia-text outline-none focus:border-maia-accent/40'}
    >
      <option value="">{placeholder}</option>
      {files.map(f => (
        <option key={f.file_id} value={f.file_id}>{f.name}</option>
      ))}
    </select>
  )
}
