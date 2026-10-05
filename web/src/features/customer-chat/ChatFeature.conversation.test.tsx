/** Component test: how the conversation behaves around a turn — waiting, failing, retrying and focus. */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { CONFIRMATION_TEXT, TurnResponseSchema } from './contracts'
import type { TurnResponse } from './contracts'
import { findMessage } from './findMessage'
import { en } from '../../i18n/en'

/** Builds a contract-valid English turn; `overrides` sets choices, language or awaited element. */
function turn(
  version: number,
  reply: string,
  overrides: Partial<Pick<TurnResponse, 'choices' | 'next_expected' | 'lang' | 'case_number'>> = {},
): TurnResponse {
  return TurnResponseSchema.parse({
    contract_version: '1',
    turn_id: `conversation-turn-${String(version).padStart(4, '0')}`,
    conversation_id: 'conversation-under-test',
    state_version: version,
    lang: 'en',
    reply,
    reference_date_line: 'Today is Thursday, 18 June 2026.',
    demo_notice: 'This is a demonstration conversation, not your real account.',
    choices: [],
    next_expected: 'transaction',
    end_session: false,
    handoff_ticket: null,
    ...overrides,
  })
}

const OPENING = turn(1, 'Hi! Which transaction would you like to dispute?')

/** One recorded send: the text and the turn id the chat attached to it. */
interface Sent {
  text: string
  turnId: string | undefined
}

/** A client whose reply to each send is chosen by the test; every send is recorded. */
function controlledClient(
  reply: (sent: Sent, count: number) => Promise<TurnResponse>,
  opening: TurnResponse = OPENING,
): { client: ChatClient; sent: Sent[] } {
  const sent: Sent[] = []
  const client: ChatClient = {
    start: () => Promise.resolve(opening),
    sendTurn: (text, turnId) => {
      const record = { text, turnId }
      sent.push(record)
      return reply(record, sent.length)
    },
  }
  return { client, sent }
}

/** Types `text` into the message field and presses Send. */
async function typeAndSend(user: ReturnType<typeof userEvent.setup>, text: string): Promise<void> {
  await user.type(screen.getByLabelText('Your message'), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
}

describe('ChatFeature around a turn', () => {
  it('shows a typing row while a reply is awaited and removes it when the reply lands', async () => {
    const user = userEvent.setup()
    let release: (value: TurnResponse) => void = () => undefined
    const { client } = controlledClient(
      () =>
        new Promise<TurnResponse>((resolve) => {
          release = resolve
        }),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    expect(screen.queryByText('The assistant is typing…')).not.toBeInTheDocument()

    await typeAndSend(user, 'the Tienda Sol one')
    expect(await screen.findByText('The assistant is typing…')).toBeInTheDocument()

    act(() => {
      release(turn(2, 'I found one transaction.'))
    })
    await findMessage('I found one transaction.')
    expect(screen.queryByText('The assistant is typing…')).not.toBeInTheDocument()
  })

  it('keeps the customer’s words on screen as not sent when the turn fails, with one Retry', async () => {
    const user = userEvent.setup()
    const { client } = controlledClient(() => Promise.reject(new Error('network is down')))
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')

    const alert = await screen.findByRole('alert')
    expect(within(alert).getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Retry' })).toHaveLength(1)
    const failed = within(screen.getByRole('list', { name: 'Messages' })).getByText(
      'the Tienda Sol one',
    )
    expect(failed.closest('li')).toHaveTextContent('Not sent')
  })

  it('resends the same message under the same turn id when Retry is pressed', async () => {
    const user = userEvent.setup()
    const { client, sent } = controlledClient((_record, count) =>
      count === 1
        ? Promise.reject(new Error('network is down'))
        : Promise.resolve(turn(2, 'I found one transaction.')),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')
    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await findMessage('I found one transaction.')
    expect(sent).toHaveLength(2)
    expect(sent[1]?.text).toBe('the Tienda Sol one')
    expect(sent[1]?.turnId).toBeDefined()
    expect(sent[1]?.turnId).toBe(sent[0]?.turnId)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('list', { name: 'Messages' })).queryByText('Not sent'),
    ).not.toBeInTheDocument()
  })

  it('puts the focus on the new Retry when a resend fails again', async () => {
    const user = userEvent.setup()
    const { client, sent } = controlledClient(() => Promise.reject(new Error('network is down')))
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')

    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await waitFor(() => {
      expect(sent).toHaveLength(2)
      expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus()
    })
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => {
      expect(sent).toHaveLength(3)
      expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus()
    })
  })

  it('does not take the focus when a send fails while it is elsewhere', async () => {
    const user = userEvent.setup()
    let fail: (error: Error) => void = () => undefined
    const { client } = controlledClient(
      () =>
        new Promise<TurnResponse>((_resolve, reject) => {
          fail = reject
        }),
    )
    render(
      <>
        <button type="button">Elsewhere</button>
        <ChatFeature client={client} lang="en" />
      </>,
    )
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' })
    elsewhere.focus()

    act(() => {
      fail(new Error('network is down'))
    })

    await screen.findByRole('alert')
    expect(elsewhere).toHaveFocus()
  })

  it('returns the keyboard to the message field after a pressed option disappears with the reply', async () => {
    const user = userEvent.setup()
    const options = turn(2, 'Which of these?', {
      choices: [{ number: 1, label: 'MXN 250.00 at Tienda Sol on 12 June 2026' }],
    })
    const { client } = controlledClient((_record, count) =>
      Promise.resolve(count === 1 ? options : turn(3, 'What is the reason for the dispute?')),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'a purchase')
    await user.click(
      await screen.findByRole('button', { name: '1. MXN 250.00 at Tienda Sol on 12 June 2026' }),
    )
    await findMessage('What is the reason for the dispute?')

    await waitFor(() => {
      expect(screen.getByLabelText('Your message')).toHaveFocus()
    })
  })

  it('leaves the focus alone when the customer has moved it elsewhere before the reply', async () => {
    const user = userEvent.setup()
    let release: (value: TurnResponse) => void = () => undefined
    const { client } = controlledClient(
      () =>
        new Promise<TurnResponse>((resolve) => {
          release = resolve
        }),
    )
    render(
      <>
        <button type="button">Elsewhere</button>
        <ChatFeature client={client} lang="en" />
      </>,
    )
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' })
    elsewhere.focus()

    act(() => {
      release(turn(2, 'I found one transaction.'))
    })
    await findMessage('I found one transaction.')
    expect(elsewhere).toHaveFocus()
  })

  it('moves the focus to the outcome when the reply ends the conversation and nothing else holds it', async () => {
    const user = userEvent.setup()
    const ending = TurnResponseSchema.parse({
      ...turn(2, 'I will pass this to a person.'),
      end_session: true,
      handoff_ticket: 'H-7730',
    })
    const { client } = controlledClient(() => Promise.resolve(ending))
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'someone used my card')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: en['chat.result.escalatedTitle'] })).toHaveFocus()
    })
  })

  it('keeps the focus in the message field when a filing arrives and the conversation goes on', async () => {
    const user = userEvent.setup()
    const filing = turn(2, 'Done, I filed your dispute.', { case_number: 'D-20481' })
    const { client } = controlledClient(() => Promise.resolve(filing))
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'yes, please file it')

    await findMessage(filing.reply)
    await waitFor(() => {
      expect(screen.getByLabelText('Your message')).toHaveFocus()
    })
    expect(screen.getByRole('heading', { name: en['chat.result.filedTitle'] })).not.toHaveFocus()
  })

  it('moves the focus to the outcome when a hand-off ends a conversation that already filed a case', async () => {
    const user = userEvent.setup()
    const filing = turn(2, 'Done, I filed your dispute.', { case_number: 'D-20481' })
    const ending = TurnResponseSchema.parse({
      ...turn(3, 'I will pass the rest to a person.'),
      end_session: true,
      handoff_ticket: 'H-7730',
    })
    const { client } = controlledClient((_sent, count) =>
      Promise.resolve(count === 1 ? filing : ending),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'yes, please file it')
    await findMessage(filing.reply)
    await typeAndSend(user, 'my card was also stolen')

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: en['chat.result.escalatedTitle'] })).toHaveFocus()
    })
  })

  it('returns the keyboard to the message field when the opening reply arrives after a Retry', async () => {
    const user = userEvent.setup()
    let starts = 0
    const client: ChatClient = {
      start: () =>
        ++starts === 1 ? Promise.reject(new Error('network is down')) : Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(new Error('not used')),
    }
    render(<ChatFeature client={client} lang="en" />)

    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await findMessage(OPENING.reply)
    await waitFor(() => {
      expect(screen.getByLabelText('Your message')).toHaveFocus()
    })
  })

  it('does not take the keyboard when the very first opening reply arrives', async () => {
    const { client } = controlledClient(() => Promise.reject(new Error('not used')))
    render(<ChatFeature client={client} lang="en" />)

    await findMessage(OPENING.reply)

    expect(screen.getByLabelText('Your message')).not.toHaveFocus()
  })

  it('changes the announcement region for every reply, even one that repeats the previous words', async () => {
    const user = userEvent.setup()
    const same = 'I could not find that transaction. Which one do you mean?'
    const { client, sent } = controlledClient((_sent, count) =>
      Promise.resolve(turn(count + 1, same)),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the blue one')
    await waitFor(() => {
      expect(sent).toHaveLength(1)
    })
    const region = screen
      .getAllByRole('status')
      .find((element) => element.getAttribute('aria-atomic') === 'true')
    if (region === undefined) throw new Error('announcement region not found')
    await waitFor(() => {
      expect(region).toHaveTextContent(same)
    })
    const changes: MutationRecord[] = []
    const observer = new MutationObserver((records) => changes.push(...records))
    observer.observe(region, { childList: true, characterData: true, subtree: true })

    await typeAndSend(user, 'the red one')
    await waitFor(() => {
      expect(sent).toHaveLength(2)
    })
    await act(() => Promise.resolve())
    observer.disconnect()

    expect(region).toHaveTextContent(same)
    expect(changes.length).toBeGreaterThan(0)
  })

  it('leaves the focus where the customer put it when the conversation ends', async () => {
    const user = userEvent.setup()
    let release: (value: TurnResponse) => void = () => undefined
    const { client } = controlledClient(
      () =>
        new Promise<TurnResponse>((resolve) => {
          release = resolve
        }),
    )
    render(
      <>
        <button type="button">Elsewhere</button>
        <ChatFeature client={client} lang="en" />
      </>,
    )
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'someone used my card')
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' })
    elsewhere.focus()

    act(() => {
      release(
        TurnResponseSchema.parse({
          ...turn(2, 'I will pass this to a person.'),
          end_session: true,
          handoff_ticket: 'H-7730',
        }),
      )
    })
    await findMessage('I will pass this to a person.')
    expect(elsewhere).toHaveFocus()
  })

  it('sends a listed option as its number and shows its full description as the customer’s words', async () => {
    const user = userEvent.setup()
    const options = turn(2, 'Which of these?', {
      choices: [
        { number: 1, label: 'MXN 250.00 at Tienda Sol on 12 June 2026' },
        { number: 2, label: 'MXN 90.00 at Supermercado Norte on 3 June 2026' },
      ],
    })
    const { client, sent } = controlledClient((_record, count) =>
      Promise.resolve(count === 1 ? options : turn(3, 'What is the reason for the dispute?')),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'a purchase')

    await user.click(
      await screen.findByRole('button', {
        name: '2. MXN 90.00 at Supermercado Norte on 3 June 2026',
      }),
    )

    await findMessage('What is the reason for the dispute?')
    expect(sent.map((record) => record.text)).toEqual(['a purchase', '2'])
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName === 'LI' &&
          element.textContent === 'You: MXN 90.00 at Supermercado Norte on 3 June 2026',
      ),
    ).toBeInTheDocument()
  })

  it('sends the fixed confirmation text and shows the button’s own label in the customer’s language', async () => {
    const user = userEvent.setup()
    const summary = turn(2, 'You are disputing MXN 250.00. File this dispute?', {
      next_expected: 'confirmation',
    })
    const { client, sent } = controlledClient((_record, count) =>
      Promise.resolve(count === 1 ? summary : turn(3, 'Your dispute was filed.')),
    )
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'the Tienda Sol one')
    await user.click(await screen.findByRole('button', { name: 'Yes, file it' }))

    await findMessage('Your dispute was filed.')
    expect(sent.map((record) => record.text)).toEqual(['the Tienda Sol one', CONFIRMATION_TEXT])
    expect(
      screen.getByText(
        (_content, element) =>
          element?.tagName === 'LI' && element.textContent === 'You: Yes, file it',
      ),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('list', { name: 'Messages' })).queryByText(CONFIRMATION_TEXT),
    ).not.toBeInTheDocument()
  })

  it('resends a listed option by its number when its turn fails and Retry is pressed', async () => {
    const user = userEvent.setup()
    const options = turn(2, 'Which of these?', {
      choices: [{ number: 1, label: 'MXN 250.00 at Tienda Sol on 12 June 2026' }],
    })
    const { client, sent } = controlledClient((_record, count) => {
      if (count === 1) return Promise.resolve(options)
      if (count === 2) return Promise.reject(new Error('network is down'))
      return Promise.resolve(turn(3, 'What is the reason for the dispute?'))
    })
    render(<ChatFeature client={client} lang="en" />)
    await findMessage(OPENING.reply)
    await typeAndSend(user, 'a purchase')
    await user.click(
      await screen.findByRole('button', { name: '1. MXN 250.00 at Tienda Sol on 12 June 2026' }),
    )
    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await findMessage('What is the reason for the dispute?')
    expect(sent.map((record) => record.text)).toEqual(['a purchase', '1', '1'])
    expect(sent[2]?.turnId).toBe(sent[1]?.turnId)
  })
})
