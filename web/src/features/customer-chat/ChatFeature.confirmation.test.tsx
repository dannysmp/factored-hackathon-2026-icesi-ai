/** Component test: the confirmation button never confirms anything but the summary on screen. */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { CONFIRMATION_TEXT, TurnResponseSchema } from './contracts'
import type { TurnResponse } from './contracts'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { findMessage } from './findMessage'

function turn(
  version: number,
  reply: string,
  nextExpected: TurnResponse['next_expected'],
): TurnResponse {
  return TurnResponseSchema.parse({
    contract_version: '1',
    turn_id: `confirmation-turn-${String(version).padStart(4, '0')}`,
    conversation_id: 'confirmation-conversation',
    state_version: version,
    lang: 'en',
    reply,
    reference_date_line: 'Today is Thursday, 18 June 2026.',
    demo_notice: 'This is a demonstration conversation, not your real account.',
    choices: [],
    next_expected: nextExpected,
    end_session: false,
    handoff_ticket: null,
  })
}

const OPENING = turn(1, 'Hi! Which transaction would you like to dispute?', 'transaction')
const SUMMARY_250 = turn(
  2,
  'You are disputing MXN 250.00 at Tienda Sol. File this dispute?',
  'confirmation',
)
const CHANGED = turn(3, 'Understood, which amount is the right one?', 'reason')
const SUMMARY_180 = turn(
  4,
  'You are disputing MXN 180.00 at Tienda Sol. File this dispute?',
  'confirmation',
)
const FILED = turn(5, 'Your dispute was filed.', null)

/** Replays `script` after the opening turn and records every text the customer sends. */
function recordingClient(script: readonly TurnResponse[]): { client: ChatClient; sent: string[] } {
  const sent: string[] = []
  let cursor = 0
  const client: ChatClient = {
    start: () => Promise.resolve(OPENING),
    sendTurn: (text) => {
      sent.push(text)
      const next = script[cursor]
      cursor += 1
      return next === undefined
        ? Promise.reject(new Error('script exhausted'))
        : Promise.resolve(next)
    },
  }
  return { client, sent }
}

async function sendText(user: ReturnType<typeof userEvent.setup>, text: string): Promise<void> {
  await user.type(screen.getByLabelText('Your message'), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ChatFeature confirmation button', () => {
  it('is not offered until the assistant asks for confirmation', async () => {
    render(<ChatFeature client={recordingClient([]).client} lang="en" />)
    await findMessage(OPENING.reply)

    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })

  it('sends only the fixed confirmation text, never a summary of its own', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')

    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    await findMessage(FILED.reply)
    expect(sent).toEqual(['the Tienda Sol one', CONFIRMATION_TEXT])
  })

  it('is withdrawn once the summary changes, and offered again only with the new summary', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, CHANGED, SUMMARY_180, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('button', { name: 'Confirm' })

    // The customer changes the amount instead of confirming: the old summary no longer applies.
    await sendText(user, 'no, the amount is wrong')
    await findMessage(CHANGED.reply)
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()

    await sendText(user, '180')
    await findMessage(SUMMARY_180.reply)
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    await findMessage(FILED.reply)
    expect(sent).toEqual([
      'the Tienda Sol one',
      'no, the amount is wrong',
      '180',
      CONFIRMATION_TEXT,
    ])
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })

  it('is offered again, once, when a second summary follows the first without a change turn', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, SUMMARY_180, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('button', { name: 'Confirm' })

    await sendText(user, 'make it 180')
    await findMessage(SUMMARY_180.reply)
    expect(screen.getAllByRole('button', { name: 'Confirm' })).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await findMessage(FILED.reply)
    expect(sent).toEqual(['the Tienda Sol one', 'make it 180', CONFIRMATION_TEXT])
  })

  it.each([
    ['es', es],
    ['pt', pt],
  ] as const)(
    'is labelled in the customer language (%s) and sends the same text',
    async (lang, catalog) => {
      const user = userEvent.setup()
      const { client, sent } = recordingClient([
        { ...SUMMARY_250, lang },
        { ...FILED, lang },
      ])
      const opening = { ...OPENING, lang }
      render(
        <ChatFeature client={{ ...client, start: () => Promise.resolve(opening) }} lang={lang} />,
      )
      await findMessage(opening.reply, catalog['chat.messagesLabel'])
      await user.type(screen.getByLabelText(catalog['chat.messageLabel']), 'x')
      await user.click(screen.getByRole('button', { name: catalog['chat.send'] }))

      await user.click(await screen.findByRole('button', { name: catalog['chat.confirm'] }))

      await findMessage(FILED.reply, catalog['chat.messagesLabel'])
      expect(sent).toEqual(['x', CONFIRMATION_TEXT])
    },
  )

  it('is disabled while a turn is in flight and confirms once', async () => {
    const user = userEvent.setup()
    const sent: string[] = []
    let release: (value: TurnResponse) => void = () => undefined
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: (text) => {
        sent.push(text)
        if (sent.length === 1) {
          return Promise.resolve(SUMMARY_250)
        }
        return new Promise<TurnResponse>((resolve) => {
          release = resolve
        })
      },
    }
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    const confirm = await screen.findByRole('button', { name: 'Confirm' })

    await user.click(confirm)
    await waitFor(() => {
      expect(confirm).toBeDisabled()
    })
    await user.click(confirm)
    act(() => {
      release(FILED)
    })

    await findMessage(FILED.reply)
    expect(sent.filter((text) => text === CONFIRMATION_TEXT)).toHaveLength(1)
  })
})
