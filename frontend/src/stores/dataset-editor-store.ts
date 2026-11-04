// @ts-nocheck - dataset editor under development
import { create } from 'zustand'
import { dataApi } from '@/services/api/data'

const BASE_URL = ''

export type EditorStep = 1 | 2 | 3

interface UploadedFile {
  id: string          // 后端登记的 fileId（唯一访问凭证）
  fileName: string
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
  uploadSubdir: string  // 上传时间戳目录，同一数据集的多次上传共用
  reasoningStream: string  // 模型思考过程（reasoning 事件累加）
  generationAbort: AbortController | null

  setDescription: (text: string) => void
  setGeneratedMd: (md: string) => void
  setStep: (s: EditorStep) => void
  setFileDescription: (fileId: string, desc: string) => void
  uploadFile: (file: File) => Promise<void>
  removeFile: (fileId: string) => void
  generateSpec: () => Promise<void>
  stopGeneration: () => void
  register: () => Promise<void>
  reset: () => void
}

export const useDatasetEditorStore = create<DatasetEditorState>((set, get) => ({
  step: 1, files: [], description: '', generatedMd: '', registeredId: null,
  isGenerating: false, error: null, uploadSubdir: '',
  reasoningStream: '', generationAbort: null,

  setDescription: (text) => set({ description: text }),
  setGeneratedMd: (md) => set({ generatedMd: md }),
  setStep: (s) => set({ step: s }),

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
          id: uploaded.fileId, fileName: uploaded.fileName,
          fileSize: uploaded.fileSize, format: uploaded.format, description: '',
        }],
      }))
    } catch { /* ignore */ }
  },

  removeFile: (fileId) => {
    fetch(`${BASE_URL}/api/file/${encodeURIComponent(fileId)}`, {
      method: 'DELETE',
    }).catch(() => {})
    set((state) => ({
      files: state.files.filter(f => f.id !== fileId),
    }))
  },

  reset: () => set({
    step: 1, files: [], description: '', generatedMd: '', registeredId: null,
    isGenerating: false, error: null, uploadSubdir: '',
    reasoningStream: '', generationAbort: null,
  }),

  generateSpec: async () => {
    const { description, files } = get()
    if (!description.trim() && files.length === 0) return

    const abort = new AbortController()
    set({ isGenerating: true, error: null, generatedMd: '', reasoningStream: '', step: 2, generationAbort: abort })
    try {
      const fileDescs = files.map(f => ({
        name: f.fileName, format: f.format, size: f.fileSize,
        description: f.description || '',
      }))
      const result = await dataApi.generateSpec(
        description, fileDescs as unknown as Record<string, unknown>[],
        (token) => set((s) => ({ generatedMd: s.generatedMd + token })),
        (token) => set((s) => ({ reasoningStream: s.reasoningStream + token })),
        abort.signal,
      )
      set({ generatedMd: result.spec_md, isGenerating: false, generationAbort: null })
    } catch (e) {
      if (abort.signal.aborted) {
        set({ isGenerating: false, generationAbort: null })
        return
      }
      set({ error: e instanceof Error ? e.message : String(e), isGenerating: false, generationAbort: null })
    }
  },

  stopGeneration: () => {
    const { generationAbort } = get()
    generationAbort?.abort()
  },

  register: async () => {
    const { generatedMd, files } = get()
    if (!generatedMd.trim()) return
    set({ isGenerating: true, error: null })
    try {
      const nameMatch = generatedMd.match(/^name:\s*(.+)$/m)
      const formats = [...new Set(files.map(f => f.format))]
      const totalSize = files.reduce((sum, f) => sum + f.fileSize, 0)
      const sourceFileIds = files.map(f => f.id)
      const result = await dataApi.register(
        generatedMd, nameMatch?.[1]?.trim() || 'Dataset',
        '', files.length, totalSize, formats, sourceFileIds,
      )
      set({ registeredId: result.dataset_id, step: 3, isGenerating: false })
      // 刷新数据空间列表
      const { useResourceStore } = await import('@/stores/resource-store')
      useResourceStore.getState().fetchDatasetsFromApi()
    } catch (e) { set({ error: e instanceof Error ? e.message : String(e), isGenerating: false }) }
  },
}))
