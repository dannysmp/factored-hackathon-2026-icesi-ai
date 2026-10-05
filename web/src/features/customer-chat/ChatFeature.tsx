import { useEffect, useRef } from 'react'
import type { JSX } from 'react'
import { ChoiceButtons } from './components/ChoiceButtons'
import { ConfirmationPrompt } from './components/ConfirmationPrompt'
import { MessageList } from './components/MessageList'
import { ReferenceBanner } from './components/ReferenceBanner'
import { ResultCard } from './components/ResultCard'
import { TurnForm } from './components/TurnForm'
import type { ChatClient } from './client'
import { resultOf } from './result'
import { useConversation } from './useConversation'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/ErrorState'
import { LiveAnnouncer } from '../../components/ui/LiveAnnouncer'
import { useT } from '../../i18n/useT'
import type { Lang } from '../../i18n/lang'
import { failureReason } from '../../i18n/failureReason'
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
 * Focus stays with the person: sending returns it to the message field, and when a reply
 * arrives while nothing holds focus (a clicked option disappeared, or a retry button went away),
 * it goes back to the field. When a resend fails again and nothing holds focus, it goes to the
 * new Retry button. A message that could not be sent stays in the conversation, marked
 * as not sent, with a single Retry that resends it under its original id.
 *
 * A screen reader is told about a new assistant reply through one hidden announcement region,
 * not by making the whole message list live: the list stays quiet, so nothing is read twice and
 * the person's own messages are never read back to them. A filed dispute and the end of the
 * conversation are shown as a result card, and the outcome and its reference are folded into that
 * same announcement on the turn that brings them.
 */
export function ChatFeature({
  client,
  lang,
  onLanguageChange,
  onSessionExpired,
}: {
  client: ChatClient
  lang: Lang
  onLanguageChange?: (lang: Lang) => void
  onSessionExpired?: () => void
}): JSX.Element {
  const conversation = useConversation(client)
  const activeLang = conversation.latest?.lang ?? lang
  const t = useT(activeLang)
  const inputRef = useRef<HTMLInputElement>(null)
  const retryRef = useRef<HTMLButtonElement>(null)
  const repliesSeen = useRef(0)
  const replyCount = conversation.messages.filter((m) => m.from === 'assistant').length

  useEffect(() => {
    onLanguageChange?.(activeLang)
  }, [activeLang, onLanguageChange])

  const expired = conversation.failure === 'unauthorized'
  useEffect(() => {
    if (expired) onSessionExpired?.()
  }, [expired, onSessionExpired])

  useEffect(() => {
    const before = repliesSeen.current
    repliesSeen.current = replyCount
    // The opening reply never moves focus: the person has not done anything yet.
    if (before > 0 && replyCount > before && document.activeElement === document.body) {
      inputRef.current?.focus()
    }
  }, [replyCount])

  // A resend that fails again removes the focused Retry button and puts a new one in its place;
  // focus follows it, so a keyboard or screen-reader user is not sent back to the top of the page.
  useEffect(() => {
    if (conversation.status === 'error' && document.activeElement === document.body) {
      retryRef.current?.focus()
    }
  }, [conversation.status])

  if (conversation.status === 'error' && conversation.latest === null) {
    return (
      <section aria-label={t('chat.regionLabel')} className={styles.chat}>
        <div className={styles.failure}>
          <ErrorState
            title={t('chat.couldNotStart')}
            reason={failureReason(conversation.failure, t)}
          >
            {!expired && (
              <Button ref={retryRef} onClick={conversation.retry}>
                {t('common.retry')}
              </Button>
            )}
          </ErrorState>
        </div>
      </section>
    )
  }

  const latest = conversation.latest
  const busy = conversation.status === 'loading'
  const ended = latest?.end_session === true
  const lastAssistant = conversation.messages.findLast((m) => m.from === 'assistant')
  const lastAssistantText = lastAssistant?.text ?? ''
  // A dispute is filed on a turn that does not end the conversation, and the farewell that ends
  // it carries no case number: the card shows the filing from the moment it happens and keeps its
  // number to the end, unless the conversation ends in a hand-off, whose own reference then leads.
  const caseNumber = latest?.handoff_ticket === null ? conversation.filedCase : null
  const handoffTicket = latest?.handoff_ticket ?? null
  const showResult = latest !== null && (ended || caseNumber !== null)
  const result = showResult ? resultOf(caseNumber, handoffTicket) : null
  // The outcome is announced on the turn that brings it and again at the end, not after every
  // reply in between.
  const announceResult = ended || (latest !== null && latest.case_number !== null)
  const closing =
    result === null || !announceResult
      ? ''
      : result.reference === null
        ? t(`chat.result.${result.variant}Title`)
        : `${t(`chat.result.${result.variant}Title`)}. ${t('chat.result.caseNumberLabel')}: ${result.reference}.`
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
      {latest !== null && (
        <MessageList
          messages={conversation.messages}
          lang={activeLang}
          pending={busy}
          reviewId={
            latest.next_expected === 'confirmation' && !ended ? (lastAssistant?.id ?? null) : null
          }
        />
      )}
      {conversation.status === 'error' && (
        <div className={styles.failure}>
          <ErrorState
            title={t('chat.couldNotSend')}
            reason={failureReason(conversation.failure, t)}
          >
            {!expired && (
              <Button ref={retryRef} onClick={conversation.retry}>
                {t('common.retry')}
              </Button>
            )}
          </ErrorState>
        </div>
      )}
      {result !== null && (
        <ResultCard lang={activeLang} caseNumber={caseNumber} handoffTicket={handoffTicket} />
      )}
      {latest !== null && !ended && (
        <>
          {!busy && <ChoiceButtons choices={latest.choices} onChoose={conversation.send} />}
          {!busy && latest.next_expected === 'confirmation' && (
            <ConfirmationPrompt onConfirm={conversation.send} lang={activeLang} />
          )}
          <TurnForm
            onSubmit={conversation.send}
            busy={busy}
            lang={activeLang}
            inputRef={inputRef}
          />
        </>
      )}
    </section>
  )
}
