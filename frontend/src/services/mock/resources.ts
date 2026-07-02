import type { IResourceService } from '@/services/types'
import type { Resource, ResourceType } from '@/types/resources'

const MOCK_RESOURCES: Record<ResourceType, Resource[]> = {
  data: [
    {
      id: 'data-001', name: 'subj01.edf', description: '受试者1的EEG原始数据，64通道，256Hz采样', type: 'data', version: '1.0.0',
      status: 'active', createdAt: '2026-06-20T14:32:00Z', updatedAt: '2026-06-20T14:32:00Z', tags: ['EEG', 'clinical', 'raw'],
      format: 'edf', filePath: '/my_project/eeg_data/subj01.edf', fileSize: 47185920, source: 'upload', lineage: [],
      qualityScore: 95,
    },
    {
      id: 'data-002', name: 'subj02.edf', description: '受试者2的EEG原始数据，64通道，256Hz采样', type: 'data', version: '1.0.0',
      status: 'active', createdAt: '2026-06-20T14:33:00Z', updatedAt: '2026-06-20T14:33:00Z', tags: ['EEG', 'clinical', 'raw'],
      format: 'edf', filePath: '/my_project/eeg_data/subj02.edf', fileSize: 48234496, source: 'upload', lineage: [],
      qualityScore: 93,
    },
    {
      id: 'data-003', name: 'anomaly_report.json', description: '异常检测结果报告', type: 'data', version: '1.0.0',
      status: 'active', createdAt: '2026-06-24T10:15:00Z', updatedAt: '2026-06-24T10:15:00Z', tags: ['result', 'anomaly'],
      format: 'json', filePath: '/my_project/results/anomaly_report.json', fileSize: 4096, source: 'generated',
      lineage: ['data-001', 'data-002'],
    },
  ],
  tool: [
    {
      id: 'tool-001', name: 'eeg_bandpass_filter', description: 'EEG带通滤波器，支持delta/theta/alpha/beta/gamma频段', type: 'tool', version: '2.1.0',
      status: 'active', createdAt: '2026-06-15T09:00:00Z', updatedAt: '2026-06-22T16:00:00Z', tags: ['EEG', '滤波', '预处理'],
      category: 'builtin', inputSpec: { formats: ['edf', 'npy'], schema: { channels: 'int', samples: 'int' } },
      outputSpec: { formats: ['edf', 'npy'] }, dependencies: ['scipy', 'numpy'], runtimeEnv: 'python',
      usageCount: 156, isUserGenerated: false,
    },
    {
      id: 'tool-002', name: 'eeg_anomaly_detector', description: '基于深度学习的EEG异常信号检测工具', type: 'tool', version: '0.1.0',
      status: 'active', createdAt: '2026-06-23T11:20:00Z', updatedAt: '2026-06-23T11:20:00Z', tags: ['EEG', '异常检测', '深度学习', '⭐本地工具'],
      category: 'local', inputSpec: { formats: ['edf'], schema: { channels: 'int', duration_s: 'float' } },
      outputSpec: { formats: ['json'], schema: { anomalies: 'array', confidence: 'float' } },
      codePath: '/tools/eeg_anomaly_detector.py', dependencies: ['torch', 'scipy', 'numpy'], runtimeEnv: 'python',
      usageCount: 23, isUserGenerated: true, relatedModelId: 'model-003',
    },
    {
      id: 'tool-003', name: 'spectral_analyzer', description: '多通道频谱分析工具，支持功率谱密度和时频分析', type: 'tool', version: '3.0.1',
      status: 'active', createdAt: '2026-05-01T08:00:00Z', updatedAt: '2026-06-20T10:00:00Z', tags: ['频谱分析', '时频分析'],
      category: 'builtin', inputSpec: { formats: ['edf', 'npy'], schema: {} },
      outputSpec: { formats: ['json', 'png'] }, dependencies: ['scipy', 'matplotlib'], runtimeEnv: 'python',
      usageCount: 89, isUserGenerated: false,
    },
    {
      id: 'tool-004', name: 'data_loader', description: '通用数据加载工具，支持EDF/CSV/NPZ/PNG等多种格式', type: 'tool', version: '1.5.0',
      status: 'active', createdAt: '2026-04-10T12:00:00Z', updatedAt: '2026-06-01T14:00:00Z', tags: ['数据加载', 'IO'],
      category: 'builtin', inputSpec: { formats: ['edf', 'csv', 'npy', 'npz', 'png', 'tiff'], schema: {} },
      outputSpec: { formats: ['npy'] }, dependencies: ['numpy', 'scipy', 'pandas', 'pillow'], runtimeEnv: 'python',
      usageCount: 342, isUserGenerated: false,
    },
    {
      id: 'tool-005', name: 'llm_text_analyzer', description: 'LLM文本分析接口工具', type: 'tool', version: '1.2.0',
      status: 'active', createdAt: '2026-06-01T10:00:00Z', updatedAt: '2026-06-10T08:00:00Z', tags: ['LLM', '文本分析'],
      category: 'model-wrapper', inputSpec: { formats: ['text'], schema: {} },
      outputSpec: { formats: ['json', 'text'] }, dependencies: [], runtimeEnv: 'python',
      usageCount: 512, isUserGenerated: false, relatedModelId: 'model-001',
    },
  ],
  model: [
    {
      id: 'model-001', name: 'SOTABand-LLM-v3', description: '多模态大语言模型，支持文本/图像理解与生成', type: 'model', version: '3.2.0',
      status: 'active', createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-20T00:00:00Z', tags: ['LLM', '多模态'],
      framework: 'PyTorch', modelType: 'multimodal', paramCount: 72000000000, weightPath: '/models/sotaband-llm-v3.pt',
      inputFormat: ['text', 'image'], outputFormat: ['text', 'json'], deploymentStatus: 'deployed',
    },
    {
      id: 'model-002', name: 'ViT-EEG-Feature', description: 'ViT架构的EEG特征提取模型', type: 'model', version: '1.0.0',
      status: 'active', createdAt: '2026-05-15T08:00:00Z', updatedAt: '2026-05-15T08:00:00Z', tags: ['ViT', 'EEG', '特征提取'],
      framework: 'PyTorch', modelType: 'timeseries', paramCount: 86000000, weightPath: '/models/vit-eeg-v1.pt',
      inputFormat: ['edf', 'npy'], outputFormat: ['npy'], accuracy: 0.923, deploymentStatus: 'deployed',
    },
    {
      id: 'model-003', name: 'EEG-Anomaly-CNN', description: '3D-CNN异常信号检测模型', type: 'model', version: '2.0.1',
      status: 'active', createdAt: '2026-06-10T10:00:00Z', updatedAt: '2026-06-23T11:00:00Z', tags: ['CNN', 'EEG', '异常检测'],
      framework: 'PyTorch', modelType: 'timeseries', paramCount: 12000000, weightPath: '/models/eeg-anomaly-cnn-v2.pt',
      inputFormat: ['npy'], outputFormat: ['json'], accuracy: 0.894, deploymentStatus: 'deployed',
    },
  ],
  agent: [
    {
      id: 'agent-001', name: '交互Agent', description: '负责与用户对话，解析需求，引导任务编排', type: 'agent', version: '1.0.0',
      status: 'active', createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z', tags: ['交互', '对话', '编排'],
      role: 'interactive', capabilities: '# 交互Agent\n\n负责与用户自然语言对话，理解用户意图，引导任务编排流程。\n\n## 能力\n- 解析用户自然语言需求\n- 调用资源发现器查询可用工具\n- 触发代码生成或任务执行\n- 实时反馈执行进度',
      requiredTools: ['tool-005'], inputFormat: 'text', outputFormat: 'text', collaborationProtocol: 'direct-message',
      healthStatus: 'healthy',
    },
    {
      id: 'agent-002', name: '数据加载Agent', description: '专用数据加载与预处理智能体', type: 'agent', version: '1.2.0',
      status: 'active', createdAt: '2026-05-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z', tags: ['数据加载', '预处理'],
      role: 'task', capabilities: '加载多格式数据（EDF/CSV/PNG等），转换为统一内部格式',
      requiredTools: ['tool-004'], inputFormat: 'file-path', outputFormat: 'npy', collaborationProtocol: 'pub-sub',
      healthStatus: 'healthy',
    },
    {
      id: 'agent-003', name: '异常检测Agent', description: 'EEG异常信号检测专用智能体', type: 'agent', version: '0.2.0',
      status: 'active', createdAt: '2026-06-23T11:30:00Z', updatedAt: '2026-06-23T11:30:00Z', tags: ['异常检测', 'EEG', '⭐新生成'],
      role: 'task', capabilities: '检测EEG数据中的异常信号，输出异常区间和置信度',
      requiredTools: ['tool-001', 'tool-002'], inputFormat: 'npy', outputFormat: 'json', collaborationProtocol: 'pub-sub',
      healthStatus: 'healthy',
    },
    {
      id: 'agent-004', name: '编排Agent', description: '负责将用户复杂需求编译为多智能体编排描述', type: 'agent', version: '1.0.0',
      status: 'active', createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z', tags: ['编排', 'DAG', '编译'],
      role: 'orchestrator', capabilities: '解析复杂任务描述，查询可用Agent/工具/模型，生成编排Markdown文件',
      requiredTools: [], inputFormat: 'text', outputFormat: 'markdown', collaborationProtocol: 'direct-message',
      healthStatus: 'healthy',
    },
  ],
  user: [
    {
      id: 'user-001', name: 'jmlv', description: '系统管理员', type: 'user', version: '1.0.0',
      status: 'active', createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z', tags: ['admin'],
      email: 'admin@sotaband.local', role: 'admin', tenantId: 'tenant-001',
      resourceQuota: { maxStorage: 107374182400, maxConcurrentTasks: 10, maxAgents: 50 },
      explorationHistory: ['task-001', 'task-002', 'task-003'],
    },
  ],
  task: [
    {
      id: 'task-001', name: 'EEG异常检测 #42', description: '对subj01.edf和subj02.edf进行异常信号检测', type: 'task', version: '1.0.0',
      status: 'active', createdAt: '2026-06-24T09:00:00Z', updatedAt: '2026-06-24T09:05:23Z', tags: ['EEG', '异常检测'],
      state: 'done', currentStep: 4, totalSteps: 4, progress: 100,
      startedAt: '2026-06-24T09:00:00Z', finishedAt: '2026-06-24T09:05:23Z',
      relatedResourceIds: ['data-001', 'data-002', 'tool-001', 'tool-002', 'agent-002', 'agent-003'],
      executionLog: [
        '[09:00:00] 任务开始',
        '[09:00:01] Agent:data_loader 加载 subj01.edf',
        '[09:00:12] Agent:data_loader 加载 subj02.edf',
        '[09:00:25] Agent:eeg_anomaly_detector 开始检测',
        '[09:05:10] 检测完成，发现 3 个异常区间',
        '[09:05:23] 结果已注册到数据空间',
      ],
    },
    {
      id: 'task-002', name: 'EEG频谱分析 #41', description: '对subj01.edf进行全通道频谱分析', type: 'task', version: '1.0.0',
      status: 'active', createdAt: '2026-06-23T10:00:00Z', updatedAt: '2026-06-23T10:03:45Z', tags: ['EEG', '频谱分析'],
      state: 'done', currentStep: 3, totalSteps: 3, progress: 100,
      startedAt: '2026-06-23T10:00:00Z', finishedAt: '2026-06-23T10:03:45Z',
      relatedResourceIds: ['data-001', 'tool-003'],
      executionLog: ['[10:00:00] 任务开始', '[10:03:45] 频谱分析完成'],
    },
    {
      id: 'task-003', name: '复杂EEG分析流程 #40', description: '加载→预处理→并行(异常检测+频谱分析)→结果汇总', type: 'task', version: '1.0.0',
      status: 'active', createdAt: '2026-06-22T14:00:00Z', updatedAt: '2026-06-22T14:08:30Z', tags: ['EEG', '多Agent编排', '复杂'],
      state: 'done', currentStep: 5, totalSteps: 5, progress: 100,
      startedAt: '2026-06-22T14:00:00Z', finishedAt: '2026-06-22T14:08:30Z',
      relatedResourceIds: ['data-001', 'tool-001', 'tool-002', 'tool-003', 'agent-002', 'agent-003', 'agent-004'],
      executionLog: [
        '[14:00:00] 编排Agent 解析任务',
        '[14:00:05] DAG 编译完成，5个节点，2个并行分支',
        '[14:00:10] 开始执行...',
        '[14:08:30] 全部完成',
      ],
    },
  ],
}

export class MockResourceService implements IResourceService {
  private resources = MOCK_RESOURCES

  async list(type: ResourceType): Promise<Resource[]> {
    return this.resources[type] || []
  }

  async get(id: string): Promise<Resource | null> {
    for (const list of Object.values(this.resources)) {
      const found = list.find(r => r.id === id)
      if (found) return found
    }
    return null
  }

  async search(query: string): Promise<Resource[]> {
    const results: Resource[] = []
    for (const list of Object.values(this.resources)) {
      for (const r of list) {
        if (r.name.includes(query) || r.description.includes(query) || r.tags.some(t => t.includes(query))) {
          results.push(r)
        }
      }
    }
    return results
  }
}
