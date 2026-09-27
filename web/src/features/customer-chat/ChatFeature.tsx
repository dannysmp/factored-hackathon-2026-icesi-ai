import type { JSX } from 'react'
import { ChoiceButtons } from './components/ChoiceButtons'
import { ConfirmationPrompt } from './components/ConfirmationPrompt'
import { MessageList } from './components/MessageList'
import { ReferenceBanner } from './components/ReferenceBanner'
import { TurnForm } from './components/TurnForm'
import type { ChatClient } from './client'
import { useConversation } from './useConversation'

/**
 * The customer chat, wired to whatever `ChatClient` its caller passes in.
 *
 * Renders every state deliberately (frontend standard, section 6): loading, error (with a
 * retryable message, never a stack trace), and ready, where the confirmation button, the choice
 * buttons and the text form each appear only when the assistant's last turn calls for them.
 */
export function ChatFeature({ client }: { client: ChatClient }): JSX.Element {
  const conversation = useConversation(client)

  if (conversation.status === 'error' && conversation.latest === null) {
    return (
      <div role="alert">
        <p>The conversation could not start. Please try again.</p>
      </div>
    )
  }

  const latest = conversation.latest
  const busy = conversation.status === 'loading'
  const ended = latest?.end_session === true

  return (
    <section aria-label="Customer chat">
      {latest !== null && (
        <ReferenceBanner
          referenceDateLine={latest.reference_date_line}
          demoNotice={latest.demo_notice}
        />
      )}
      <MessageList messages={conversation.messages} />
      {conversation.status === 'error' && (
        <p role="alert">Your last message could not be sent. Please try again.</p>
      )}
      {latest !== null && !ended && (
        <>
          <ChoiceButtons choices={latest.choices} onChoose={conversation.send} disabled={busy} />
          {latest.next_expected === 'confirmation' && (
            <ConfirmationPrompt onConfirm={conversation.send} disabled={busy} />
          )}
          <TurnForm onSubmit={conversation.send} disabled={busy} />
        </>
      )}
    </section>
  )
}
