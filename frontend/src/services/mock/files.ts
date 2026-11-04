import type { IFileService } from '@/services/types'
import type { FileTreeNode } from '@/types/workspace'

const MOCK_FILE_TREE: FileTreeNode = {
  id: 'root',
  name: 'my_project',
  type: 'directory',
  category: 'folder',
  expanded: true,
  children: [
    {
      id: 'dir-eeg',
      name: 'eeg_data',
      type: 'directory',
      category: 'folder',
      expanded: false,
      children: [
        {
          id: 'file-subj01',
          name: 'subj01.edf',
          type: 'file',
          category: 'eeg',
          fileId: 'f_000000000001',
          format: 'edf',
          size: 47185920,
        },
        {
          id: 'file-subj02',
          name: 'subj02.edf',
          type: 'file',
          category: 'eeg',
          fileId: 'f_000000000002',
          format: 'edf',
          size: 48234496,
        },
        {
          id: 'file-labels',
          name: 'labels.csv',
          type: 'file',
          category: 'table',
          fileId: 'f_000000000003',
          format: 'csv',
          size: 2048,
        },
      ],
    },
    {
      id: 'dir-images',
      name: 'images',
      type: 'directory',
      category: 'folder',
      expanded: false,
      children: [
        {
          id: 'file-scan01',
          name: 'scan01.png',
          type: 'file',
          category: 'image',
          fileId: 'f_000000000004',
          format: 'png',
          size: 2097152,
        },
        {
          id: 'file-scan02',
          name: 'scan02.png',
          type: 'file',
          category: 'image',
          fileId: 'f_000000000005',
          format: 'png',
          size: 1835008,
        },
      ],
    },
    {
      id: 'dir-results',
      name: 'results',
      type: 'directory',
      category: 'folder',
      expanded: false,
      children: [
        {
          id: 'file-output',
          name: 'anomaly_report.json',
          type: 'file',
          category: 'result',
          fileId: 'f_000000000006',
          format: 'json',
          size: 4096,
        },
        {
          id: 'file-viz',
          name: 'spectrogram.png',
          type: 'file',
          category: 'image',
          fileId: 'f_000000000007',
          format: 'png',
          size: 524288,
        },
      ],
    },
    {
      id: 'file-notes',
      name: 'notes.md',
      type: 'file',
      category: 'text',
      fileId: 'f_000000000008',
      format: 'md',
      size: 1024,
    },
  ],
}

export class MockFileService implements IFileService {
  private tree: FileTreeNode = JSON.parse(JSON.stringify(MOCK_FILE_TREE))

  async getTree(): Promise<FileTreeNode> {
    return this.tree
  }

  async search(query: string): Promise<FileTreeNode[]> {
    const results: FileTreeNode[] = []
    const searchNode = (node: FileTreeNode) => {
      if (node.name.toLowerCase().includes(query.toLowerCase())) {
        results.push(node)
      }
      node.children?.forEach(searchNode)
    }
    searchNode(this.tree)
    return results
  }

  async upload(files: File[]): Promise<FileTreeNode[]> {
    const newNodes: FileTreeNode[] = files.map((file, i) => ({
      id: `file-upload-${Date.now()}-${i}`,
      name: file.name,
      type: 'file' as const,
      category: 'unknown' as const,
      fileId: `f_0000000000${(10 + i).toString(16).padStart(2, '0')}`,
      format: file.name.split('.').pop(),
      size: file.size,
    }))

    this.tree.children = [...(this.tree.children || []), ...newNodes]
    return newNodes
  }
}
