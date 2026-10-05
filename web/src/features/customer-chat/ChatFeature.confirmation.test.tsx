/** Component test: the confirmation quick replies and their review frame. */
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { CONFIRMATION_TEXT, DECLINE_TEXT, TurnResponseSchema } from './contracts'
import type { TurnResponse } from './contracts'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { findMessage } from './findMessage'

/** Builds a contract-valid English turn with the given reply and awaited element. */
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
const CANCELLED = turn(3, "Understood, I didn't file the dispute.", null)
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

/** Types `text` into the message field and presses Send. */
async function sendText(user: ReturnType<typeof userEvent.setup>, text: string): Promise<void> {
  await user.type(screen.getByLabelText('Your message'), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ChatFeature confirmation button', () => {
  it('is not offered until the assistant asks for confirmation', async () => {
    render(<ChatFeature client={recordingClient([]).client} lang="en" />)
    await findMessage(OPENING.reply)

    expect(screen.queryByRole('button', { name: 'Yes, file it' })).not.toBeInTheDocument()
  })

  it('sends only the fixed confirmation text, never a summary of its own', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')

    await user.click(await screen.findByRole('button', { name: 'Yes, file it' }))

    await findMessage(FILED.reply)
    expect(sent).toEqual(['the Tienda Sol one', CONFIRMATION_TEXT])
  })

  it('is withdrawn once the summary changes, and offered again only with the new summary', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, CHANGED, SUMMARY_180, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('button', { name: 'Yes, file it' })

    // The customer changes the amount instead of confirming: the old summary no longer applies.
    await sendText(user, 'no, the amount is wrong')
    await findMessage(CHANGED.reply)
    expect(screen.queryByRole('button', { name: 'Yes, file it' })).not.toBeInTheDocument()

    await sendText(user, '180')
    await findMessage(SUMMARY_180.reply)
    await user.click(await screen.findByRole('button', { name: 'Yes, file it' }))

    await findMessage(FILED.reply)
    expect(sent).toEqual([
      'the Tienda Sol one',
      'no, the amount is wrong',
      '180',
      CONFIRMATION_TEXT,
    ])
    expect(screen.queryByRole('button', { name: 'Yes, file it' })).not.toBeInTheDocument()
  })

  it('is offered again, once, when a second summary follows the first without a change turn', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, SUMMARY_180, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('button', { name: 'Yes, file it' })

    await sendText(user, 'make it 180')
    await findMessage(SUMMARY_180.reply)
    expect(screen.getAllByRole('button', { name: 'Yes, file it' })).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Yes, file it' }))
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

  it('offers a No beside the Yes that sends only the fixed decline text', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, CANCELLED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    const group = await screen.findByRole('group', { name: 'Your answer' })
    expect(group).toContainElement(screen.getByRole('button', { name: 'Yes, file it' }))

    await user.click(screen.getByRole('button', { name: "No, don't file" }))

    await findMessage(CANCELLED.reply)
    expect(sent).toEqual(['the Tienda Sol one', DECLINE_TEXT])
    expect(
      screen.getByText((_c, el) => el?.textContent === "You: No, don't file"),
    ).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Your answer' })).not.toBeInTheDocument()
    expect(screen.queryByText('Review before filing')).not.toBeInTheDocument()
  })

  it.each([
    ['es', es, 'De acuerdo, no presenté la disputa.'],
    ['pt', pt, 'Tudo bem, não apresentei a contestação.'],
  ] as const)('labels the No in the customer language (%s)', async (lang, catalog, cancelled) => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([
      { ...SUMMARY_250, lang },
      { ...CANCELLED, lang, reply: cancelled },
    ])
    const opening = { ...OPENING, lang }
    render(
      <ChatFeature client={{ ...client, start: () => Promise.resolve(opening) }} lang={lang} />,
    )
    await findMessage(opening.reply, catalog['chat.messagesLabel'])
    await user.type(screen.getByLabelText(catalog['chat.messageLabel']), 'x')
    await user.click(screen.getByRole('button', { name: catalog['chat.send'] }))

    await user.click(await screen.findByRole('button', { name: catalog['chat.decline'] }))

    await findMessage(cancelled, catalog['chat.messagesLabel'])
    expect(sent).toEqual(['x', DECLINE_TEXT])
    expect(
      screen.getByText(
        (_c, el) =>
          el?.textContent === `${catalog['chat.customerLabel']} ${catalog['chat.decline']}`,
      ),
    ).toBeInTheDocument()
  })

  it('shows the review frame with the quick replies, only while confirmation is awaited', async () => {
    const user = userEvent.setup()
    const { client } = recordingClient([SUMMARY_250, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    expect(screen.queryByText('Review before filing')).not.toBeInTheDocument()
    await sendText(user, 'the Tienda Sol one')

    const summary = (await findMessage(SUMMARY_250.reply)).closest('li')
    expect(summary).not.toHaveTextContent('Review before filing')
    const group = await screen.findByRole('group', { name: 'Your answer' })
    const frame = screen.getByText('Review before filing').parentElement
    expect(frame).toContainElement(group)
    expect(group).toHaveAccessibleDescription('Nothing is filed until you say yes.')

    await user.click(screen.getByRole('button', { name: 'Yes, file it' }))
    await findMessage(FILED.reply)
    expect(screen.queryByText('Review before filing')).not.toBeInTheDocument()
  })

  it('keeps one review frame beside the quick replies through replies that do not answer it', async () => {
    const user = userEvent.setup()
    const clarify = turn(3, 'The dispute covers a single charge. Shall I file it?', 'confirmation')
    const smallTalk = turn(4, 'Happy to help with anything else too.', 'confirmation')
    const { client } = recordingClient([SUMMARY_250, clarify, smallTalk])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await findMessage(SUMMARY_250.reply)

    for (const reply of [clarify.reply, smallTalk.reply]) {
      await sendText(user, 'what does that mean?')
      const message = await findMessage(reply)
      await screen.findByRole('group', { name: 'Your answer' })
      expect(screen.getAllByText('Review before filing')).toHaveLength(1)
      expect(message.closest('li')).not.toHaveTextContent('Review before filing')
      expect(screen.getByRole('list')).not.toHaveTextContent('Review before filing')
    }
  })

  it('shows one review frame when a changed summary is issued while confirmation is pending', async () => {
    const user = userEvent.setup()
    const { client } = recordingClient([SUMMARY_250, SUMMARY_180])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await findMessage(SUMMARY_250.reply)

    await sendText(user, 'actually it was 180')
    await findMessage(SUMMARY_180.reply)

    await screen.findByRole('group', { name: 'Your answer' })
    expect(screen.getAllByText('Review before filing')).toHaveLength(1)
    expect(screen.getByRole('list')).not.toHaveTextContent('Review before filing')
  })

  it('is withdrawn while a turn is in flight, so it confirms once', async () => {
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
    const confirm = await screen.findByRole('button', { name: 'Yes, file it' })

    await user.click(confirm)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Yes, file it' })).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: "No, don't file" })).not.toBeInTheDocument()
    act(() => {
      release(FILED)
    })

    await findMessage(FILED.reply)
    expect(sent.filter((text) => text === CONFIRMATION_TEXT)).toHaveLength(1)
  })

  it('has no accessibility violations at the confirmation', async () => {
    const user = userEvent.setup()
    const { client } = recordingClient([SUMMARY_250])
    const { container } = render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('group', { name: 'Your answer' })

    expect(await axe(container)).toHaveNoViolations()
  })

  it('shows neither the review nor the quick replies once the conversation has ended', async () => {
    const user = userEvent.setup()
    const closing = TurnResponseSchema.parse({ ...SUMMARY_250, end_session: true })
    const { client } = recordingClient([closing])
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')

    await findMessage(closing.reply)
    expect(screen.queryByText('Review before filing')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Your answer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Yes, file it' })).not.toBeInTheDocument()
  })
})
