import { useChatStore } from '@/stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'

import { Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)
  const clearMessages = useChatStore((s) => s.clearMessages)

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-1 border-b border-maia-border bg-maia-bg/30 shrink-0">
        <span className="text-[11px] text-maia-text-muted">{messages.length} 条消息</span>
        <Button
          variant="ghost"
          size="sm"
          className="text-[11px] h-6 text-maia-text-muted hover:text-red-500"
          onClick={() => { if (messages.length > 1) clearMessages() }}
        >
          <Trash2 className="h-3 w-3 mr-1" />
          清空对话
        </Button>
      </div>
      <div className="flex-1 min-h-0">
        <MessageList messages={messages} isSending={isSending} />
      </div>
      <ChatInput />
    </div>
  )
}
