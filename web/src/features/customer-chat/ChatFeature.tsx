import { useEffect } from 'react'
import type { JSX } from 'react'
import { ChoiceButtons } from './components/ChoiceButtons'
import { ConfirmationPrompt } from './components/ConfirmationPrompt'
import { MessageList } from './components/MessageList'
import { ReferenceBanner } from './components/ReferenceBanner'
import { TurnForm } from './components/TurnForm'
import type { ChatClient } from './client'
import { useConversation } from './useConversation'
import { LiveAnnouncer } from '../../components/ui/LiveAnnouncer'
import { useT } from '../../i18n/useT'
import type { Lang } from '../../i18n/lang'
import styles from './ChatFeature.module.css'

/**
 * The customer chat, wired to whatever `ChatClient` its caller passes in.
 *
 * Renders every state deliberately (frontend standard, section 6): loading, error (with a
 * retryable message, never a stack trace), and ready, where the confirmation button, the choice
 * buttons and the text form each appear only when the assistant's last turn calls for them.
 *
 * `lang` is the persona's selected language, known from sign-in before any turn exists; once a
 * turn arrives, its own `lang` (the server's grounded value) takes over, so the chrome never
 * drifts from what the conversation itself is actually in. `onLanguageChange` reports that
 * language so the page around the chat (its title, its document language) can follow it.
 *
 * A screen reader is told about a new assistant reply through one hidden announcement region,
 * not by making the whole message list live: the list stays quiet, so nothing is read twice and
 * the person's own messages are never read back to them. When the conversation ends, the closing
 * line and the case reference are folded into that same announcement.
 */
export function ChatFeature({
  client,
  lang,
  onLanguageChange,
}: {
  client: ChatClient
  lang: Lang
  onLanguageChange?: (lang: Lang) => void
}): JSX.Element {
  const conversation = useConversation(client)
  const activeLang = conversation.latest?.lang ?? lang
  const t = useT(activeLang)

  useEffect(() => {
    onLanguageChange?.(activeLang)
  }, [activeLang, onLanguageChange])

  if (conversation.status === 'error' && conversation.latest === null) {
    return (
      <div role="alert" className={styles.error}>
        <p>{t('chat.couldNotStart')}</p>
      </div>
    )
  }

  const latest = conversation.latest
  const busy = conversation.status === 'loading'
  const ended = latest?.end_session === true
  const lastAssistantText =
    conversation.messages.findLast((m) => m.from === 'assistant')?.text ?? ''
  const closing =
    ended && latest.handoff_ticket !== null
      ? `${t('chat.ended')} ${t('chat.caseReference').replace('{ticket}', latest.handoff_ticket)}`
      : ended
        ? t('chat.ended')
        : ''
  const announcement = [lastAssistantText, closing].filter((part) => part !== '').join(' ')

  return (
    <section aria-label={t('chat.regionLabel')} className={styles.chat}>
      <LiveAnnouncer message={announcement} />
      {latest !== null && (
        <ReferenceBanner
          referenceDateLine={latest.reference_date_line}
          demoNotice={latest.demo_notice}
        />
      )}
      {latest === null && <p className={styles.status}>{t('chat.starting')}</p>}
      {latest !== null && <MessageList messages={conversation.messages} lang={activeLang} />}
      {conversation.status === 'error' && (
        <p role="alert" className={styles.error}>
          {t('chat.couldNotSend')}
        </p>
      )}
      {latest !== null && !ended && (
        <>
          <ChoiceButtons choices={latest.choices} onChoose={conversation.send} disabled={busy} />
          {latest.next_expected === 'confirmation' && (
            <ConfirmationPrompt onConfirm={conversation.send} disabled={busy} lang={activeLang} />
          )}
          <TurnForm onSubmit={conversation.send} disabled={busy} lang={activeLang} />
        </>
      )}
      {ended && (
        <p className={styles.ended}>
          {t('chat.ended')}
          {latest.handoff_ticket !== null && (
            <> {t('chat.caseReference').replace('{ticket}', latest.handoff_ticket)}</>
          )}
        </p>
      )}
    </section>
  )
}
