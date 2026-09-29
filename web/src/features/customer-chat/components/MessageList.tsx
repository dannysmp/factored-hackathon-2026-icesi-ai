import type { JSX } from 'react'
import type { Message } from '../useConversation'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
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
export function MessageList({
  messages,
  lang,
}: {
  messages: readonly Message[]
  lang: Lang
}): JSX.Element {
  const t = useT(lang)

  if (messages.length === 0) {
    return (
      <p className={styles.empty} aria-live="polite">
        {t('chat.noMessagesYet')}
      </p>
    )
  }
  return (
    <ol className={styles.list} aria-live="polite" aria-label="Conversation">
      {messages.map((message) => (
        <li key={message.id} className={styles.message}>
          <span className={styles.from}>
            {message.from === 'assistant' ? t('chat.assistantLabel') : t('chat.customerLabel')}{' '}
          </span>
          {message.text}
        </li>
      ))}
    </ol>
  )
}
