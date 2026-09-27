import type { JSX } from 'react'
import type { Message } from '../useConversation'

/**
 * The conversation so far.
 *
 * `aria-live="polite"` announces each new assistant reply to a screen reader without moving
 * focus (frontend standard, section 7: "dynamic content that updates without navigation uses a
 * polite live region"); the customer's own messages need no announcement, since typing them was
 * already the customer's own action.
 */
export function MessageList({ messages }: { messages: readonly Message[] }): JSX.Element {
  return (
    <ol aria-live="polite" aria-label="Conversation">
      {messages.map((message) => (
        <li key={message.id}>
          <span>{message.from === 'assistant' ? 'Assistant: ' : 'You: '}</span>
          {message.text}
        </li>
      ))}
    </ol>
  )
}
