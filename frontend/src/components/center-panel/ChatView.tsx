import { useChatStore } from '@/stores/chat-store'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'

export function ChatView() {
  const messages = useChatStore((s) => s.messages)
  const isSending = useChatStore((s) => s.isSending)

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0">
        <MessageList messages={messages} isSending={isSending} />
      </div>
      <ChatInput />
    </div>
  )
}
