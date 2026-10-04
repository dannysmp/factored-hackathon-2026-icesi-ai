/** Component test: the confirmation button never confirms anything but the summary on screen. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { CONFIRMATION_TEXT, TurnResponseSchema } from './contracts'
import type { TurnResponse } from './contracts'

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
    await screen.findByText(OPENING.reply)

    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })

  it('sends only the fixed confirmation text, never a summary of its own', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await screen.findByText(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')

    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    await screen.findByText(FILED.reply)
    expect(sent).toEqual(['the Tienda Sol one', CONFIRMATION_TEXT])
  })

  it('is withdrawn once the summary changes, and offered again only with the new summary', async () => {
    const user = userEvent.setup()
    const { client, sent } = recordingClient([SUMMARY_250, CHANGED, SUMMARY_180, FILED])
    render(<ChatFeature client={client} lang="en" />)
    await screen.findByText(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    await screen.findByRole('button', { name: 'Confirm' })

    // The customer changes the amount instead of confirming: the old summary no longer applies.
    await sendText(user, 'no, the amount is wrong')
    await screen.findByText(CHANGED.reply)
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()

    await sendText(user, '180')
    await screen.findByText(SUMMARY_180.reply)
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    await screen.findByText(FILED.reply)
    expect(sent).toEqual([
      'the Tienda Sol one',
      'no, the amount is wrong',
      '180',
      CONFIRMATION_TEXT,
    ])
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })

  it('is disabled while a turn is in flight, so a second click cannot confirm twice', async () => {
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
    await screen.findByText(OPENING.reply)
    await sendText(user, 'the Tienda Sol one')
    const confirm = await screen.findByRole('button', { name: 'Confirm' })

    await user.click(confirm)
    await waitFor(() => {
      expect(confirm).toBeDisabled()
    })
    await user.click(confirm)
    release(FILED)

    await screen.findByText(FILED.reply)
    expect(sent.filter((text) => text === CONFIRMATION_TEXT)).toHaveLength(1)
  })
})
