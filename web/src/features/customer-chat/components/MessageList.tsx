import { useEffect, useRef } from 'react'
import type { JSX } from 'react'
import type { Message } from '../useConversation'
import { useT } from '../../../i18n/useT'
import type { Lang } from '../../../i18n/lang'
import { classNames } from '../../../components/ui/classNames'
import styles from './MessageList.module.css'

/**
 * The conversation so far.
 *
 * The list itself is not a live region: new replies are announced by the chat's own hidden
 * announcement, so the history is never re-read when a message is added. Renders a
 * purpose-built empty state (not a bare, silent list) when nothing has arrived yet, so this
 * component reads correctly on its own regardless of whatever loading text a caller shows
 * alongside it.
 *
 * The customer's messages sit on the right in a filled accent bubble and the assistant's on the
 * left in a quiet outlined one, each with a sender label so the speaker never depends on position
 * or color alone. The message that asks for confirmation (`reviewId`) is set apart as a review
 * card, headed by what it is and followed by the reassurance that nothing is filed until the
 * customer says yes. While a reply is awaited a typing bubble closes the list; it lives in an
 * always-present status region so it is announced once, politely, when it appears. When a message is added or the typing row
 * appears, the newest content is scrolled into view: a reply from its first line, the person's
 * own message and the typing row at the nearest edge.
 */
export function MessageList({
  messages,
  lang,
  pending,
  reviewId = null,
}: {
  messages: readonly Message[]
  lang: Lang
  pending: boolean
  reviewId?: string | null
}): JSX.Element {
  const t = useT(lang)
  const lastRef = useRef<HTMLLIElement>(null)
  const typingRef = useRef<HTMLDivElement>(null)
  const seen = useRef({ count: messages.length, pending })
  const newest = messages.at(-1)

  useEffect(() => {
    const before = seen.current
    seen.current = { count: messages.length, pending }
    const arrived = messages.length > before.count
    const typingStarted = pending && !before.pending
    if (!arrived && !typingStarted) {
      return
    }
    if (pending) {
      typingRef.current?.scrollIntoView({ block: 'nearest' })
    } else {
      lastRef.current?.scrollIntoView({ block: newest?.from === 'assistant' ? 'start' : 'nearest' })
    }
  }, [messages.length, pending, newest?.from])

  if (messages.length === 0) {
    return <p className={styles.empty}>{t('chat.noMessagesYet')}</p>
  }
  return (
    <>
      <ol className={styles.list} aria-label={t('chat.messagesLabel')}>
        {messages.map((message, index) => (
          <li
            key={message.id}
            ref={index === messages.length - 1 ? lastRef : undefined}
            className={classNames(
              styles.message,
              message.from === 'customer' ? styles.customer : styles.assistant,
              message.failed === true && styles.failed,
              message.id === reviewId && styles.review,
            )}
          >
            {message.id === reviewId && (
              <span className={styles.reviewTitle}>{t('chat.review.title')}</span>
            )}
            <span className={styles.from}>
              {message.from === 'assistant' ? t('chat.assistantLabel') : t('chat.customerLabel')}
            </span>{' '}
            <span className={styles.text}>{message.text}</span>
            {message.failed === true && <span className={styles.notSent}>{t('chat.notSent')}</span>}
            {message.id === reviewId && (
              <span className={styles.reviewHint}>{t('chat.review.hint')}</span>
            )}
          </li>
        ))}
      </ol>
      <div role="status" ref={typingRef} className={styles.typing}>
        {pending && (
          <span className={styles.typingBubble}>
            <span className={styles.dots} aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            {t('chat.assistantTyping')}
          </span>
        )}
      </div>
    </>
  )
}
