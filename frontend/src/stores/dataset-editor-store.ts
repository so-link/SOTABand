import { create } from 'zustand'
import { dataApi } from '@/services/api/data'

export type EditorStep = 1 | 2 | 3

interface UploadedFile {
  id: string
  fileName: string
  filePath: string
  fileSize: number
  format: string
  description: string  // 用户为该文件写的描述
}

interface DatasetEditorState {
  step: EditorStep
  files: UploadedFile[]
  description: string
  generatedMd: string
  registeredId: string | null
  isGenerating: boolean
  error: string | null

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  setStep: (s: EditorStep) => void
  setFileDescription: (fileId: string, desc: string) => void
  uploadFile: (file: File) => Promise<void>
  removeFile: (fileId: string) => void
  generateSpec: () => Promise<void>
  register: () => Promise<void>
  reset: () => void
}

export const useDatasetEditorStore = create<DatasetEditorState>((set, get) => ({
  step: 1, files: [], description: '', generatedMd: '', registeredId: null,
  isGenerating: false, error: null,

  setDescription: (text) => set({ description: text }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setStep: (s) => set({ step: s }),

  setFileDescription: (fileId, desc) => set((state) => ({
    files: state.files.map(f => f.id === fileId ? { ...f, description: desc } : f),
  })),

  uploadFile: async (file: File) => {
    const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001'
    const formData = new FormData()
    formData.append('file', file)
    try {
      const res = await fetch(`${BASE_URL}/api/file/upload`, {
        method: 'POST', body: formData,
      })
      if (!res.ok) return
      const uploaded = await res.json()
      set((state) => ({
        files: [...state.files, {
          id: uploaded.id, fileName: uploaded.fileName, filePath: uploaded.filePath,
          fileSize: uploaded.fileSize, format: uploaded.format, description: '',
        }],
      }))
    } catch { /* ignore */ }
  },

  removeFile: (fileId) => set((state) => ({
    files: state.files.filter(f => f.id !== fileId),
  })),

  reset: () => set({
    step: 1, files: [], description: '', generatedMd: '', registeredId: null,
    isGenerating: false, error: null,
  }),

  generateSpec: async () => {
    const { description, files } = get()
    if (!description.trim() && files.length === 0) return
    set({ isGenerating: true, error: null })
    try {
      // 构建带文件描述的信息传给后端
      const fileDescs = files.map(f => ({
        name: f.fileName, format: f.format, size: f.fileSize,
        description: f.description || '',
        path: f.filePath,
      }))
      const result = await dataApi.generateSpec(description, fileDescs as unknown as Record<string, unknown>[])
      set({ generatedMd: result.spec_md, step: 2, isGenerating: false })
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },

  register: async () => {
    const { generatedMd, files } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const nameMatch = generatedMd.match(/^name:\s*(.+)$/m)
      const formats = [...new Set(files.map(f => f.format))]
      const totalSize = files.reduce((sum, f) => sum + f.fileSize, 0)
      const sourceFiles = files.map(f => f.filePath)
      const result = await dataApi.register(
        generatedMd, nameMatch?.[1]?.trim() || 'Dataset',
        '', files.length, totalSize, formats, sourceFiles,
      )
      set({ registeredId: result.dataset_id, step: 3, isGenerating: false })
      // 刷新数据空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchDatasetsFromApi()
    } catch (e) { set({ error: String(e), isGenerating: false }) }
  },
}))
