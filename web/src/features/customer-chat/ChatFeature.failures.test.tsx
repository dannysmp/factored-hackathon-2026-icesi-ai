/** Component test: what the chat says, and lets the person do, for each way a turn can fail. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { TurnRequestError } from './client'
import { TurnResponseSchema } from './contracts'
import { findMessage } from './findMessage'
import { en } from '../../i18n/en'
import { pt } from '../../i18n/pt'

const OPENING = TurnResponseSchema.parse({
  contract_version: '1',
  turn_id: 'failure-turn-0001',
  conversation_id: 'conversation-under-test',
  state_version: 1,
  lang: 'en',
  reply: 'Hi! Which transaction would you like to dispute?',
  reference_date_line: 'Today is Thursday, 18 June 2026.',
  demo_notice: null,
  choices: [],
  next_expected: 'transaction',
  end_session: false,
  handoff_ticket: null,
})

const FAILURES = [
  ['a limit', new TurnRequestError(429, 'Too many'), en['failure.rateLimited']],
  ['a server error', new TurnRequestError(503, 'Unavailable'), en['failure.unavailable']],
  ['no connection', new TypeError('Failed to fetch'), en['failure.offline']],
  ['a timeout', new DOMException('timed out', 'TimeoutError'), en['failure.timeout']],
] as const

async function sendAndFail(client: ChatClient): Promise<HTMLElement> {
  const user = userEvent.setup()
  render(<ChatFeature client={client} lang="en" />)
  await findMessage(OPENING.reply)
  await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  return screen.findByRole('alert')
}

describe('ChatFeature when a message cannot be sent', () => {
  it.each(FAILURES)('says why after %s, with Retry', async (_name, error, reason) => {
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(error),
    }
    const alert = await sendAndFail(client)

    expect(alert).toHaveTextContent(en['chat.couldNotSend'])
    expect(alert).toHaveTextContent(reason)
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('adds no reason for a failure it cannot name', async () => {
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(new Error('odd')),
    }
    const alert = await sendAndFail(client)

    expect(alert).toHaveTextContent(`${en['chat.couldNotSend']}Retry`)
  })

  it('speaks the conversation’s language', async () => {
    const client: ChatClient = {
      start: () => Promise.resolve({ ...OPENING, lang: 'pt' }),
      sendTurn: () => Promise.reject(new TypeError('Failed to fetch')),
    }
    const user = userEvent.setup()
    render(<ChatFeature client={client} lang="pt" />)
    await user.type(await screen.findByLabelText(pt['chat.messageLabel']), 'oi')
    await user.click(screen.getByRole('button', { name: pt['chat.send'] }))

    expect(await screen.findByRole('alert')).toHaveTextContent(pt['failure.offline'])
  })

  it('has no automatically detectable accessibility violations', async () => {
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(new TypeError('Failed to fetch')),
    }
    await sendAndFail(client)

    expect(await axe(document.body)).toHaveNoViolations()
  })
})

describe('ChatFeature when the conversation cannot start', () => {
  it('names the region, says why and offers Retry', async () => {
    const client: ChatClient = {
      start: () => Promise.reject(new TypeError('Failed to fetch')),
      sendTurn: () => Promise.reject(new Error('unused')),
    }
    render(<ChatFeature client={client} lang="en" />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(en['chat.couldNotStart'])
    expect(alert).toHaveTextContent(en['failure.offline'])
    expect(screen.getByRole('region', { name: en['chat.regionLabel'] })).toContainElement(alert)
    expect(await axe(document.body)).toHaveNoViolations()
  })

  it('starts the conversation again when Retry is pressed', async () => {
    const user = userEvent.setup()
    const start = vi
      .fn<ChatClient['start']>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(OPENING)
    render(
      <ChatFeature
        client={{ start, sendTurn: () => Promise.reject(new Error('unused')) }}
        lang="en"
      />,
    )

    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    await findMessage(OPENING.reply)
    expect(start).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('ChatFeature when the session has ended', () => {
  it('tells its owner once, and offers no Retry that could not work', async () => {
    const onSessionExpired = vi.fn()
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(new TurnRequestError(401, 'Sign in required')),
    }
    const user = userEvent.setup()
    render(<ChatFeature client={client} lang="en" onSessionExpired={onSessionExpired} />)
    await findMessage(OPENING.reply)
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(en['app.sessionExpired'])
    expect(within(alert).queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
  })

  it('tells its owner when the session had already ended before the conversation started', async () => {
    const onSessionExpired = vi.fn()
    const client: ChatClient = {
      start: () => Promise.reject(new TurnRequestError(401, 'Sign in required')),
      sendTurn: () => Promise.reject(new Error('unused')),
    }
    render(<ChatFeature client={client} lang="en" onSessionExpired={onSessionExpired} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(en['app.sessionExpired'])
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(onSessionExpired).toHaveBeenCalledTimes(1)
  })

  it('does not report an expiry for any other failure', async () => {
    const onSessionExpired = vi.fn()
    const client: ChatClient = {
      start: () => Promise.resolve(OPENING),
      sendTurn: () => Promise.reject(new TurnRequestError(503, 'Unavailable')),
    }
    const user = userEvent.setup()
    render(<ChatFeature client={client} lang="en" onSessionExpired={onSessionExpired} />)
    await findMessage(OPENING.reply)
    await user.type(screen.getByLabelText('Your message'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('alert')

    expect(onSessionExpired).not.toHaveBeenCalled()
  })
})
