import { useChatStore } from '@/stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'
import { ChatEmptyState } from './ChatEmptyState'
import { Trash2 } from 'lucide-react'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const clearMessages = useChatStore((s) => s.clearMessages)

  return (
    <div className="flex flex-col h-full">
      {messages.length > 0 && (
        <div className="flex items-center justify-end px-4 pt-2 shrink-0">
          <button
            onClick={() => clearMessages()}
            className="inline-flex items-center gap-1 h-6 px-1.5 rounded text-[13px] text-maia-text-muted hover:text-maia-danger hover:bg-maia-danger-bg transition-colors"
          >
            <Trash2 className="h-3 w-3" />
            清空对话
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0">
        {messages.length === 0 ? (
          <ChatEmptyState />
        ) : (
          <MessageList messages={messages} isSending={isSending} />
        )}
      </div>

      <ChatInput />
    </div>
  )
}
