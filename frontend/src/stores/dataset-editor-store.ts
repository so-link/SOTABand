// @ts-nocheck - dataset editor under development
import { create } from 'zustand'
import { dataApi } from '@/services/api/data'

const BASE_URL = ''

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
  generatedTags: string[]
  registeredId: string | null
  isGenerating: boolean
  error: string | null
  uploadSubdir: string  // 上传时间戳目录，同一数据集的多次上传共用

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  setStep: (s: EditorStep) => void
  addTag: (tag: string) => void
  removeTag: (tag: string) => void
  setFileDescription: (fileId: string, desc: string) => void
  uploadFile: (file: File) => Promise<void>
  removeFile: (fileId: string) => void
  generateSpec: () => Promise<void>
  register: () => Promise<void>
  reset: () => void
}

export const useDatasetEditorStore = create<DatasetEditorState>((set, get) => ({
  step: 1, files: [], description: '', generatedMd: '', generatedTags: [], registeredId: null,
  isGenerating: false, error: null, uploadSubdir: '',

  setDescription: (text) => set({ description: text }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setStep: (s) => set({ step: s }),
  addTag: (tag: string) => set((s) => ({
    generatedTags: s.generatedTags.includes(tag) ? s.generatedTags : [...s.generatedTags, tag],
  })),
  removeTag: (tag: string) => set((s) => ({
    generatedTags: s.generatedTags.filter(t => t !== tag),
  })),

  setFileDescription: (fileId, desc) => set((state) => ({
    files: state.files.map(f => f.id === fileId ? { ...f, description: desc } : f),
  })),

  uploadFile: async (file: File) => {
    const BASE_URL = ''
    const formData = new FormData()
    formData.append('file', file)

    // 首次上传生成时间戳目录，后续上传复用
    let { uploadSubdir } = get()
    if (!uploadSubdir) {
      uploadSubdir = `dataset_${Date.now()}`
      set({ uploadSubdir })
    }
    formData.append('subdir', uploadSubdir)

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

  removeFile: (fileId) => {
    const file = get().files.find(f => f.id === fileId)
    if (file?.filePath) {
      fetch(`${BASE_URL}/api/file/delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: file.filePath }),
      }).catch(() => {})
    }
    set((state) => ({
      files: state.files.filter(f => f.id !== fileId),
    }))
  },

  reset: () => set({
    step: 1, files: [], description: '', generatedMd: '', generatedTags: [], registeredId: null,
    isGenerating: false, error: null, uploadSubdir: '',
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
      set({ generatedMd: result.spec_md, generatedTags: result.tags || [], step: 2, isGenerating: false })
    } catch (e) { set({ error: e instanceof Error ? e.message : String(e), isGenerating: false }) }
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
        get().generatedTags,
      )
      set({ registeredId: result.dataset_id, step: 3, isGenerating: false })
      // 刷新数据空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchDatasetsFromApi()
    } catch (e) { set({ error: e instanceof Error ? e.message : String(e), isGenerating: false }) }
  },
}))
