import type { JSX } from 'react'
import type { Message } from '../useConversation'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import styles from './MessageList.module.css'

/**
 * The conversation so far.
 *
 * The list itself is not a live region: new replies are announced by the chat's own hidden
 * announcement, so the history is never re-read when a message is added. Renders a
 * purpose-built empty state (not a bare, silent list) when nothing has arrived yet, so this
 * component reads correctly on its own regardless of whatever loading text a caller shows
 * alongside it.
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
    return <p className={styles.empty}>{t('chat.noMessagesYet')}</p>
  }
  return (
    <ol className={styles.list} aria-label={t('chat.messagesLabel')}>
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
