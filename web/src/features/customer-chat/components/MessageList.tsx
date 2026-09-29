import type { JSX } from 'react'
import type { Message } from '../useConversation'
import styles from './MessageList.module.css'

/**
 * The conversation so far.
 *
 * `aria-live="polite"` announces each new assistant reply to a screen reader without moving
 * focus (frontend standard, section 7: "dynamic content that updates without navigation uses a
 * polite live region"); the customer's own messages need no announcement, since typing them was
 * already the customer's own action. Renders a purpose-built empty state (not a bare, silent
 * list) when nothing has arrived yet, so this component reads correctly on its own regardless of
 * whatever loading text a caller shows alongside it.
 */
export function MessageList({ messages }: { messages: readonly Message[] }): JSX.Element {
  if (messages.length === 0) {
    return (
      <p className={styles.empty} aria-live="polite">
        No messages yet.
      </p>
    )
  }
  return (
    <ol className={styles.list} aria-live="polite" aria-label="Conversation">
      {messages.map((message) => (
        <li key={message.id} className={styles.message}>
          <span className={styles.from}>
            {message.from === 'assistant' ? 'Assistant: ' : 'You: '}
          </span>
          {message.text}
        </li>
      ))}
    </ol>
  )
}
