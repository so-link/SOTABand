import {
  File, Activity, Image, Table, FileText, Box, Archive, FileOutput, Folder,
} from 'lucide-react'
import type { FileCategory } from '@/types/workspace'

/** 文件类别 → 图标（与「我的文件」树、消息气泡保持一致） */
const FILE_ICONS: Record<FileCategory, typeof File> = {
  eeg: Activity,
  image: Image,
  table: Table,
  text: FileText,
  model: Box,
  archive: Archive,
  result: FileOutput,
  folder: Folder,
  unknown: File,
}

/** 按文件名取类别（与 file-tree-store 的 CATEGORY_BY_EXT 同规则） */
const CATEGORY_BY_EXT: Record<string, FileCategory> = {
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', bmp: 'image', webp: 'image', svg: 'image', ico: 'image',
  csv: 'table', tsv: 'table', xlsx: 'table', xls: 'table', json: 'table',
  md: 'text', txt: 'text', log: 'text', markdown: 'text',
  pt: 'model', pth: 'model', onnx: 'model', h5: 'model', safetensors: 'model',
  zip: 'archive', tar: 'archive', gz: 'archive', rar: 'archive', '7z': 'archive',
  set: 'eeg', edf: 'eeg', bdf: 'eeg',
}

export function categoryOfName(name: string): FileCategory {
  const ext = (name.split('.').pop() || '').toLowerCase()
  return CATEGORY_BY_EXT[ext] || 'unknown'
}

/** 按文件名渲染对应类别的文件图标 */
export function FileCategoryIcon({ name, className }: { name: string; className?: string }) {
  const Icon = FILE_ICONS[categoryOfName(name)] || File
  return <Icon className={className || 'h-3 w-3'} />
}
