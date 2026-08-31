import { useState, useEffect } from 'react'
import {
  FileText, ChevronRight, ChevronDown, MessageSquareWarning,
  Loader2, Check, X, Eye, EyeOff, Sparkles, Lightbulb,
} from 'lucide-react'
import type { SpecOutline, SpecNode, RefineResult } from '@/types/spec-outline'
import { NODE_TYPE_META } from '@/types/spec-outline'

interface Props {
  outline: SpecOutline
  toolId: string
  onApply?: (updatedMd: string, newOutline: SpecOutline) => void
}

/**
 * 去掉标题中的章节编号前缀（如 "3.1 标准输出字段" → "标准输出字段"）。
 *
 * 节点已通过缩进体现层次，再用编号会显得冗余；
 * 尤其子标题在分组标题之下时，"3.1" 这类前缀毫无必要。
 */
function cleanTitle(title: string): string {
  return (title || '').replace(/^\d+(\.\d+)*[\.、]?\s*/, '') || title
}

/**
 * 判断节点是否为「纯容器」：只有标题、正文为空，内容全在子节点里。
 *
 * 例：
 *   ## 3. 输出规范          ← 正文为空，是个容器
 *   ### 3.1 标准输出字段    ← 真正的内容
 *
 * 这类节点若当作普通卡片展示，展开后只看到一行标题，毫无信息量。
 * 应渲染为**分组标题**，子节点缩进其下，层次才清晰。
 */
function isEmptyContainer(node: SpecNode): boolean {
  if (!node.children.length) return false
  const body = (node.content_md || '')
    .split('\n')
    .map((l) => l.trim())
    // 去掉标题行、空行后，若没有任何实质内容 → 纯容器
    .filter((l) => l && !l.startsWith('#'))
  return body.length === 0
}

/**
 * 生成节点的原文预览（供未展开时显示）。
 *
 * 让使用者不点开也能大致判断这段内容，省去一次点击。
 * 按内容形态分别提取关键项：
 * - 表格行：取首列（跳过表头与分隔行，避免 "| 参数名 | 类型 |" 这类冗余）
 * - 列表项：去掉 `-` / `*`
 * - 编号步骤：保留编号，便于识别步骤顺序
 * - 普通段落：取前两行
 */
function previewOf(node: SpecNode): string {
  const lines = (node.content_md || '')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'))

  // 纯容器节点（只有子标题、无正文）：提示子段落数更有用
  if (!lines.length) {
    return node.children.length ? `含 ${node.children.length} 个子段落` : ''
  }

  const items: string[] = []
  let sawTableHeader = false

  for (const raw of lines) {
    if (items.length >= 3) break

    // 表格分隔行（|---|---|）：标记表头已结束，跳过
    if (/^\|[\s:|-]+\|$/.test(raw)) {
      sawTableHeader = true
      continue
    }

    if (raw.startsWith('|')) {
      // 表格：取首列
      const first = raw.split('|').filter((c) => c.trim())[0]?.trim()
      // 表头行在遇到分隔行之前，跳过（如 "| 参数名 | 类型 |"）
      if (first && !sawTableHeader) continue
      if (first) items.push(first)
      continue
    }

    // 列表项：去掉符号；编号步骤：保留编号
    const cleaned = raw.replace(/^[-*+]\s+/, '')
    if (cleaned) items.push(cleaned)
  }

  if (!items.length) return node.children.length
    ? `含 ${node.children.length} 个子段落`
    : ''

  const p = items.join('  ·  ')
  return p.length > 110 ? p.slice(0, 110) + '…' : p
}

/**
 * 规范文档概览层
 *
 * 面向非专业使用者：用「做什么 / 要什么 / 给什么 / 怎么算」的卡片式结构
 * 呈现文档要点，每个卡片锚定到技术文档的具体节点。
 *
 * 使用者发现某段不对时，可点「这段有问题」直接针对该节点提意见，
 * 系统会只重写这一段 —— 而不是重新生成整篇文档。
 */
export function SpecOutlineView({ outline, toolId, onApply }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [refiningId, setRefiningId] = useState<string | null>(null)
  const [feedback, setFeedback] = useState('')
  const [result, setResult] = useState<RefineResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [showRaw, setShowRaw] = useState<Set<string>>(new Set())
  // 已等待秒数 + 实时生成的片段：让 10 秒级的模型思考有可见进展，
  // 而不是一个静止的"正在生成…"（等待无反馈是体验差的主因）
  const [elapsed, setElapsed] = useState(0)
  const [streamText, setStreamText] = useState('')

  useEffect(() => {
    if (!busy) { setElapsed(0); return }
    const t = setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => clearInterval(t)
  }, [busy])

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleRaw = (id: string) => {
    setShowRaw((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const submitRefine = async (node: SpecNode) => {
    const text = feedback.trim()
    if (!text || busy) return
    setBusy(true); setError(''); setStreamText('')
    try {
      const { toolApi } = await import('@/services/api/tool')
      // 流式：思考阶段有计时器，生成阶段实时显示内容，
      // 避免长时间无反馈被误认为卡住
      const res = await toolApi.refineSpecNodeStream(
        { toolId, nodeId: node.id, feedback: text, save: false },
        (t) => setStreamText((s) => s + t),
      )
      setResult(res)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
      setStreamText('')
    }
  }

  const applyResult = () => {
    if (!result) return
    onApply?.(result.updated_md, result.outline)
    setResult(null); setFeedback(''); setRefiningId(null)
  }

  if (!outline.nodes.length) {
    return (
      <div className="text-xs text-maia-text-muted p-3 border border-maia-border rounded">
        {outline.warning || '该文档暂无可解析的结构'}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* 头部：说明 + 技术文档开关 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-maia-accent" />
          <span className="text-xs font-medium text-maia-text-secondary">文档要点</span>
          <span className="text-[10px] text-maia-text-muted">
            {outline.nodes.length} 个段落
          </span>
        </div>
        <span className="text-[10px] text-maia-text-muted">
          点「这段有问题」可只改这一段
        </span>
      </div>

      {/* 节点卡片 */}
      <div className="space-y-1.5">
        {outline.nodes.map((n) => {
          const meta = NODE_TYPE_META[n.node_type] || NODE_TYPE_META.section
          const isOpen = expanded.has(n.id)
          const showRawMd = showRaw.has(n.id)
          const isRefining = refiningId === n.id

          // 纯容器节点（只有标题、无正文）→ 渲染为分组标题，
          // 避免"展开后只有一行标题"的空卡片占版面
          if (isEmptyContainer(n)) {
            return (
              // px-2.5 与占位箭头(w-3 + gap-2)：让分组标题的类型标签
              // 与卡片内的类型标签左侧对齐（卡片有 border+padding+箭头）
              <div key={n.id} className="pt-2 pb-0.5 px-2.5">
                <div className="flex items-center gap-2">
                  <span className="w-3 shrink-0" />
                  <span className={`text-[10px] font-medium ${meta.color}`}>
                    {meta.label}
                  </span>
                  <span className="text-[10px] text-maia-text-muted">
                    {cleanTitle(n.title)}
                  </span>
                  <span className="h-px flex-1 bg-maia-border" />
                  <span className="text-[10px] text-maia-text-muted">
                    {n.children.length} 项
                  </span>
                </div>
              </div>
            )
          }

          // 子标题节点（如 3.1）：缩进显示，且不重复父级分组的类型标签
          const isSub = n.level >= 3

          return (
            <div
              key={n.id}
              className={`rounded-lg border transition-colors ${
                isSub ? 'ml-3 border-maia-border/60' : ''
              } ${
                isRefining ? 'border-amber-500/50 bg-amber-500/5' : 'border-maia-border'
              }`}
            >
              {/* 卡片头：整行可点击展开，避免"点箭头→再点查看原文"的两步操作 */}
              <div
                onClick={() => toggle(n.id)}
                className="flex items-start gap-2 px-2.5 py-2 cursor-pointer select-none hover:bg-maia-bg/20"
              >
                <span className="mt-0.5 text-maia-text-muted">
                  {isOpen
                    ? <ChevronDown className="h-3 w-3" />
                    : <ChevronRight className="h-3 w-3" />}
                </span>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {/* 子标题不再重复父级分组的类型标签，避免"给什么 给什么" */}
                    {!isSub && (
                      <span className={`text-[10px] font-medium ${meta.color}`}>
                        {meta.label}
                      </span>
                    )}
                    <span className="text-[11px] text-maia-text">
                      {n.summary || cleanTitle(n.title)}
                    </span>
                  </div>
                  {/* 未展开时显示原文前两行作为预览：
                      不展开也能大致判断这段内容，省去一次点击 */}
                  {!isOpen && (
                    <div className="text-[10px] text-maia-text-muted mt-0.5 font-mono truncate">
                      {previewOf(n)}
                    </div>
                  )}
                </div>

                {/* 独立按钮：阻止冒泡，避免点它时误触发整行展开 */}
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    setRefiningId(isRefining ? null : n.id)
                    setResult(null); setError('')
                  }}
                  title="针对这一段提修改意见"
                  className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-maia-border text-amber-500 hover:bg-amber-500/10 shrink-0"
                >
                  <MessageSquareWarning className="h-2.5 w-2.5" />
                  这段有问题
                </button>
              </div>

              {/* 展开后直接显示技术原文，不再需要二次点击 */}
              {isOpen && (
                <div className="px-2.5 pb-2 ml-5">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] text-maia-text-muted">
                      技术原文
                    </span>
                    <button
                      onClick={() => toggleRaw(n.id)}
                      className="flex items-center gap-1 text-[10px] text-maia-text-muted hover:text-maia-accent"
                    >
                      {showRawMd
                        ? <><EyeOff className="h-2.5 w-2.5" />收起</>
                        : <><Eye className="h-2.5 w-2.5" />展开</>}
                    </button>
                  </div>
                  <pre className={`text-[10px] font-mono text-maia-text-muted whitespace-pre-wrap bg-maia-bg/50 rounded p-2 ${
                    showRawMd ? 'max-h-96' : 'max-h-16'
                  } overflow-auto`}>
                    {n.content_md}
                  </pre>
                </div>
              )}

              {/* 精化输入区 */}
              {isRefining && (
                <div className="px-2.5 pb-2.5 ml-5 space-y-2">
                  <div className="text-[10px] text-maia-text-muted">
                    说明「{n.summary || cleanTitle(n.title)}」有什么问题，例如
                    <span className="text-maia-text-secondary">
                      「并发数默认值改成 4」
                    </span>
                    （⌘/Ctrl + Enter 提交）
                  </div>
                  <textarea
                    ref={(el) => {
                      // 展开后自动聚焦，省去一次点击
                      if (el && isRefining) {
                        requestAnimationFrame(() => el.focus())
                      }
                    }}
                    value={feedback}
                    onChange={(e) => setFeedback(e.target.value)}
                    onKeyDown={(e) => {
                      // Cmd/Ctrl+Enter 直接提交，省去移动鼠标点按钮
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter'
                          && feedback.trim() && !busy) {
                        e.preventDefault()
                        void submitRefine(n)
                      }
                    }}
                    placeholder="描述这一段哪里不对、希望改成什么样"
                    rows={3}
                    className="w-full rounded border border-maia-border bg-maia-bg/50 px-2 py-1.5 text-[11px] outline-none resize-y focus:border-amber-500/40"
                  />
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => submitRefine(n)}
                      disabled={busy || !feedback.trim()}
                      className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-maia-border text-amber-500 hover:bg-amber-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {busy
                        ? <Loader2 className="h-2.5 w-2.5 animate-spin" />
                        : <Sparkles className="h-2.5 w-2.5" />}
                      {busy ? `生成中 ${elapsed}s` : '生成修改'}
                    </button>
                    <button
                      onClick={() => { setRefiningId(null); setResult(null); setError('') }}
                      className="text-[10px] px-2 py-0.5 rounded border border-maia-border text-maia-text-muted"
                    >
                      取消
                    </button>
                  </div>

                  {/* 等待反馈：计时器 + 实时生成内容。
                      该模型思考阶段可达 10~16 秒，无反馈会被认为卡住。 */}
                  {busy && (
                    <div className="space-y-1 rounded border border-maia-border/60 p-2 bg-maia-bg/20">
                      <div className="flex items-center gap-1.5 text-[10px] text-maia-text-muted">
                        <Loader2 className="h-2.5 w-2.5 animate-spin text-amber-500" />
                        <span>
                          {streamText
                            ? '正在生成修改内容…'
                            : `模型思考中… 已等待 ${elapsed} 秒（复杂改动可能需要 15~30 秒）`}
                        </span>
                      </div>
                      {streamText && (
                        <pre className="text-[9px] font-mono text-maia-text whitespace-pre-wrap max-h-28 overflow-auto bg-maia-bg rounded p-1.5">
                          {streamText}
                        </pre>
                      )}
                    </div>
                  )}

                  {error && (
                    <div className="text-[10px] text-maia-danger">{error}</div>
                  )}

                  {/* 改前/改后对比 */}
                  {result && result.node_id === n.id && (
                    <div className="space-y-1.5 rounded border border-maia-border p-2 bg-maia-bg/30">
                      {result.impact_hint && (
                        <div className="flex items-start gap-1 text-[10px] text-amber-500">
                          <Lightbulb className="h-3 w-3 shrink-0 mt-0.5" />
                          <span>{result.impact_hint}</span>
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <div className="text-[10px] text-maia-text-muted mb-0.5">改前</div>
                          <pre className="text-[9px] font-mono text-maia-text-muted whitespace-pre-wrap max-h-32 overflow-auto bg-maia-bg rounded p-1.5">
                            {result.diff.before}
                          </pre>
                        </div>
                        <div>
                          <div className="text-[10px] text-maia-success mb-0.5">改后</div>
                          <pre className="text-[9px] font-mono text-maia-text whitespace-pre-wrap max-h-32 overflow-auto bg-maia-bg rounded p-1.5">
                            {result.diff.after}
                          </pre>
                        </div>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={applyResult}
                          className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-maia-border text-maia-success hover:bg-maia-success/10"
                        >
                          <Check className="h-2.5 w-2.5" />应用
                        </button>
                        <button
                          onClick={() => setResult(null)}
                          className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-maia-border text-maia-text-muted"
                        >
                          <X className="h-2.5 w-2.5" />放弃
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
