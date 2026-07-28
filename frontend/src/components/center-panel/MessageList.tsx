import { useEffect, useRef } from 'react'
import type { Message } from '@/types/chat'
import { MessageBubble } from './MessageBubble'
import { Loader2 } from 'lucide-react'

interface MessageListProps {
  messages: Message[]
  isSending: boolean
}

export function MessageList({ messages, isSending }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const prevCountRef = useRef(messages.length)
  const shouldAutoScrollRef = useRef(true)

  // 检测用户是否手动往上滚了（如果滚了就不自动滚）
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      // 如果距离底部超过 80px，说明用户手动往上滚了
      shouldAutoScrollRef.current = scrollHeight - scrollTop - clientHeight < 80
    }
    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [])

  // 新消息到达时：强制滚到底部并重置自动滚动标志
  useEffect(() => {
    if (messages.length > prevCountRef.current) {
      shouldAutoScrollRef.current = true
    }
    prevCountRef.current = messages.length
    if (shouldAutoScrollRef.current) {
      containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'smooth' })
    }
  }, [messages.length])

  // 流式输出时：用 ResizeObserver 监听内容区高度变化，持续滚动
  useEffect(() => {
    const content = contentRef.current
    if (!content) return
    const observer = new ResizeObserver(() => {
      if (shouldAutoScrollRef.current) {
        containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'auto' })
      }
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [])

  // isSending 变为 true 时也滚到底部
  useEffect(() => {
    if (isSending) {
      shouldAutoScrollRef.current = true
      containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight, behavior: 'auto' })
    }
  }, [isSending])

  return (
    <div ref={containerRef} className="h-full overflow-auto px-4 py-4">
      <div ref={contentRef} className="max-w-3xl mx-auto flex flex-col gap-4">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isSending && (
          <div className="flex items-center gap-2 text-gray-400 text-xs py-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            处理中...
          </div>
        )}
      </div>
    </div>
  )
}
