import { useEffect, useRef } from 'react'
import type { Message } from '@/types/chat'
import { MessageBubble } from './MessageBubble'
import { Loader2 } from 'lucide-react'

interface MessageListProps {
  messages: Message[]
  isSending: boolean
}

export function MessageList({ messages, isSending }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="h-full overflow-auto px-4 py-4">
      <div className="max-w-3xl mx-auto flex flex-col gap-4">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {/* Streaming indicator */}
        {isSending && (
          <div className="flex items-center gap-2 text-gray-400 text-xs py-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            Agent 正在思考...
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  )
}
