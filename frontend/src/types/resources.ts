// ============================================================
// 资源层统一类型定义
// ============================================================

/** 资源状态 */
export type ResourceStatus = 'active' | 'deprecated' | 'archived' | 'building'

/** 资源基类 */
export interface BaseResource {
  id: string
  name: string
  description: string
  type: string
  version: string
  status: ResourceStatus
  createdAt: string
  updatedAt: string
  tags: string[]
}

// ---- 数据空间 ----

export interface DataResource extends BaseResource {
  type: 'data'
  format: string           // EDF, CSV, PNG, NPY, etc.
  schema?: Record<string, string>  // column → dtype
  filePath: string
  fileSize: number         // bytes
  source: 'upload' | 'generated' | 'external'
  lineage: string[]        // IDs of upstream data resources
  qualityScore?: number    // 0–100
}

// ---- 工具空间 ----

export interface ToolResource extends BaseResource {
  type: 'tool'
  category: 'builtin' | 'local' | 'model-wrapper'
  inputSpec: {
    formats: string[]
    schema?: Record<string, string>
  }
  outputSpec: {
    formats: string[]
    schema?: Record<string, string>
  }
  codePath?: string        // 本地工具代码路径
  dependencies: string[]   // pip packages
  runtimeEnv: 'python' | 'node' | 'wasm'
  usageCount: number
  isUserGenerated: boolean // ⭐ 用户探索生成
  relatedModelId?: string
}

// ---- 模型空间 ----

export interface ModelResource extends BaseResource {
  type: 'model'
  framework: string        // PyTorch, ONNX, TensorFlow, etc.
  modelType: 'llm' | 'vision' | 'timeseries' | 'audio' | 'multimodal'
  paramCount?: number
  weightPath: string
  inputFormat: string[]
  outputFormat: string[]
  accuracy?: number
  deploymentStatus: 'registered' | 'deployed' | 'offline'
  relatedToolId?: string   // 关联的调用工具 ID
}

// ---- 智能体空间 ----

export interface AgentResource extends BaseResource {
  type: 'agent'
  role: 'interactive' | 'task' | 'orchestrator' | 'observer'
  capabilities: string     // Markdown 能力描述
  requiredTools: string[]  // 依赖工具 ID 列表
  inputFormat: string
  outputFormat: string
  collaborationProtocol: string
  healthStatus: 'healthy' | 'degraded' | 'offline'
  usageExample?: string
}

// ---- 用户空间 ----

export interface UserResource extends BaseResource {
  type: 'user'
  email: string
  role: 'admin' | 'developer' | 'viewer'
  tenantId: string
  resourceQuota: {
    maxStorage: number
    maxConcurrentTasks: number
    maxAgents: number
  }
  explorationHistory: string[]  // 任务 ID 列表
}

// ---- 任务空间 ----

export type TaskState = 'pending' | 'running' | 'done' | 'failed' | 'cancelled'

export interface TaskResource extends BaseResource {
  type: 'task'
  state: TaskState
  currentStep: number
  totalSteps: number
  progress: number         // 0–100
  startedAt?: string
  finishedAt?: string
  relatedResourceIds: string[]
  executionLog: string[]
  workflowDef?: string     // Markdown 编排文件
}

// ---- 联合类型 ----

export type Resource =
  | DataResource
  | ToolResource
  | ModelResource
  | AgentResource
  | UserResource
  | TaskResource

export type ResourceType = Resource['type']
