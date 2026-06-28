import type { IChatService } from '@/services/types'
import type { Message, InlineCard, CreateMessageInput } from '@/types/chat'

const RESPONSE_TEMPLATES: Array<{ trigger: string[]; response: string; cards?: InlineCard[] }> = [
  {
    trigger: ['分析', 'EEG', '异常', '脑电', '信号'],
    response: `好的，我来分析这批 EEG 数据中的异常信号。

**第一步：理解数据格式**

我查看了你选择的 EEG 数据文件。这是一个标准的 EDF 格式文件，包含 64 通道，采样率 256Hz。数据质量评分 95/100。

**第二步：查找可用工具**

我在工具空间中找到了匹配的工具。其中 \`eeg_anomaly_detector\` 是你在上次探索中生成的本地工具 ⭐，可以直接使用。`,
    cards: [
      {
        id: 'card-data-preview',
        type: 'data-preview',
        title: '数据预览：subj01.edf',
        summary: '64 通道 · 256Hz 采样率 · 10分钟时长 · 45MB',
        data: {
          format: 'EDF',
          channels: 64,
          sampleRate: 256,
          duration: '10:32',
          size: '45.2 MB',
          qualityScore: 95,
        },
      },
      {
        id: 'card-tool-match',
        type: 'tool-match',
        title: '工具匹配结果',
        summary: '找到 2 个可用工具',
        data: {
          tools: [
            { name: 'eeg_anomaly_detector', version: '0.1.0', match: 92, isLocal: true, status: 'ready' },
            { name: 'eeg_bandpass_filter', version: '2.1.0', match: 85, isLocal: false, status: 'ready' },
          ],
          suggestion: '建议使用 eeg_anomaly_detector 直接进行异常检测',
        },
      },
    ],
  },
  {
    trigger: ['频谱', '频段', 'frequency', 'spectral'],
    response: `收到！我来对 subj01.edf 进行频谱分析。

资源发现器找到了 \`spectral_analyzer\` v3.0.1，这是一个成熟的内置工具，已使用 89 次。

该工具支持：
- 功率谱密度 (PSD) 分析
- 短时傅里叶变换 (STFT) 时频分析
- 多通道并行计算
- 结果可视化为频谱图

是否需要调整频段范围？默认分析 delta (0.5-4Hz), theta (4-8Hz), alpha (8-13Hz), beta (13-30Hz), gamma (30-45Hz)。`,
    cards: [
      {
        id: 'card-tool-match-1',
        type: 'tool-match',
        title: '工具匹配：spectral_analyzer v3.0.1',
        summary: '匹配度 96% · 已使用 89 次',
        data: { tools: [{ name: 'spectral_analyzer', version: '3.0.1', match: 96, isLocal: false, status: 'ready' }] },
      },
    ],
  },
  {
    trigger: ['过滤', '滤波', '预处理', '去噪', 'filter'],
    response: `我需要先对数据进行带通滤波预处理。

工具空间中已有 \`eeg_bandpass_filter\` v2.1.0，这是一个经过验证的内置工具。但如果你需要特定的滤波参数或算法，我可以自动生成定制代码。

请确认滤波参数：
- 频段：默认 0.5-45Hz 带通
- 滤波器类型：Butterworth 4阶
- 是否需要陷波滤波器去除 50Hz 工频干扰？`,
    cards: [],
  },
  {
    trigger: ['编排', '复杂', '流程', 'pipeline', '多步'],
    response: `理解，这是一个涉及多个处理步骤的复杂任务。让我调用编排Agent来生成任务编排描述。

我分析了你的需求，涉及以下步骤：
1. **数据加载** — 加载多文件 EEG 数据
2. **预处理** — 带通滤波 + 去工频干扰
3. **并行分析** — 异常检测 ‖ 频谱分析
4. **结果汇总** — 生成综合报告

正在生成编排描述文件...`,
    cards: [
      {
        id: 'card-orchestration',
        type: 'orchestration-preview',
        title: '编排预览：EEG综合分析流程',
        summary: '5个Agent · 2个并行分支 · 预计耗时 3min',
        data: {
          agentCount: 5,
          parallelBranches: 2,
          estimatedTime: '3min',
          agents: ['data_loader', 'preprocessor', 'anomaly_detector', 'spectral_analyzer', 'aggregator'],
        },
      },
    ],
  },
]

/** 默认响应（无匹配关键词时） */
function getDefaultResponse(text: string): { response: string; cards?: InlineCard[] } {
  const lowerText = text.toLowerCase()

  // 尝试匹配模板
  for (const template of RESPONSE_TEMPLATES) {
    if (template.trigger.some(kw => lowerText.includes(kw.toLowerCase()))) {
      return { response: template.response, cards: template.cards }
    }
  }

  // 检查是否提及代码生成
  if (lowerText.includes('生成') || lowerText.includes('代码') || lowerText.includes('工具') || lowerText.includes('没有')) {
    return {
      response: `我检查了工具空间，目前没有完全匹配你需求的工具。

不过没关系！我可以让资源构建器自动编写代码来实现这个功能。代码生成后会提交给你核验，核验通过后自动注册为本地工具，后续类似需求可直接调用。

**预计生成的工具：**
- 输入：根据你的数据格式
- 输出：根据你的需求
- 语言：Python
- 依赖：将自动分析并列出

请确认是否开始生成代码？`,
      cards: [],
    }
  }

  // 通用响应
  return {
    response: `收到你的需求：「${text}」

让我来分析一下。首先让我查看当前工作区间的数据情况，然后查询工具空间看是否有匹配的工具可以使用。

当前工作区间有 4 个数据集可用，工具空间有 8 个工具。你可以：
- 📎 从左侧拖拽数据文件到对话框，将其作为任务输入
- 🔧 浏览工具空间查看可用工具
- 🤖 指定要使用的 Agent

请提供更多细节，我好为你精准匹配资源。`,
    cards: [],
  }
}

export class MockChatService implements IChatService {
  async *sendMessage(input: CreateMessageInput): AsyncGenerator<Message> {
    const userText = input.content
    const { response, cards } = getDefaultResponse(userText)

    // 模拟 Agent "思考"延迟
    await delay(800 + Math.random() * 1200)

    // 逐字符输出（模拟流式响应）
    let streamingContent = ''
    const words = response.split('')
    const chunkSize = 3 + Math.floor(Math.random() * 5)

    for (let i = 0; i < words.length; i += chunkSize) {
      streamingContent += words.slice(i, i + chunkSize).join('')
      await delay(15 + Math.random() * 25)

      yield {
        id: `msg-stream-${Date.now()}`,
        role: 'agent',
        content: streamingContent,
        timestamp: new Date().toISOString(),
        cards: i + chunkSize >= words.length ? cards : undefined, // 仅在最后一块带 cards
      }
    }
  }
}

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
