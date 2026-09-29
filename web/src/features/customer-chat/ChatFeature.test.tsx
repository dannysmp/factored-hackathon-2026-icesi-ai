/** Component test: the whole scripted conversation, its four async states, and accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'

function renderChat(): ReturnType<typeof render> {
  return render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} />)
}

describe('ChatFeature', () => {
  it('shows only its own loading text while starting, never MessageList’s empty state too', () => {
    const pending = (): Promise<never> =>
      new Promise(() => {
        // never resolves: simulates the chat still starting
      })
    const client: ChatClient = { start: pending, sendTurn: pending }
    render(<ChatFeature client={client} />)
    expect(screen.getByText('Starting the conversation…')).toBeInTheDocument()
    expect(screen.queryByText('No messages yet.')).not.toBeInTheDocument()
  })

  it('shows the reference-date line and the demonstration notice', async () => {
    renderChat()
    expect(await screen.findByText('Today is Thursday, 18 June 2026.')).toBeInTheDocument()
    expect(
      screen.getByText('This is a demonstration conversation, not your real account.'),
    ).toBeInTheDocument()
  })

  it('walks the whole scripted conversation: a choice, free text, then confirmation', async () => {
    const user = userEvent.setup()
    renderChat()

    await screen.findByText('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const choice = await screen.findByRole('button', {
      name: '1. MXN 250.00 at Tienda Sol on 12 June 2026',
    })
    await user.click(choice)

    await screen.findByText(/what is the reason for the dispute/i)
    await user.type(screen.getByLabelText('Your message'), 'unrecognized charge')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const confirm = await screen.findByRole('button', { name: 'Confirm' })
    await user.click(confirm)

    expect(await screen.findByText(/case DEMO-1234/)).toBeInTheDocument()
    expect(
      screen.getByText((_content, element) => element?.textContent === 'You: unrecognized charge'),
    ).toBeInTheDocument()
  })

  it('disables the form and shows a distinct ended state once the assistant ends the session', async () => {
    // Start straight from the last, session-ending turn.
    render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN.slice(-1))} />)
    await screen.findByText('Thanks for reaching out. Have a good day!')
    expect(screen.queryByLabelText('Your message')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('This conversation has ended.')
  })

  it('shows the case reference on the ended state when the last turn carries one', async () => {
    const [lastTurn] = FILE_DISPUTE_EN.slice(-1)
    if (lastTurn === undefined) {
      throw new Error('fixture FILE_DISPUTE_EN must have at least one turn')
    }
    const endedWithTicket = { ...lastTurn, handoff_ticket: 'DEMO-1234' }
    render(<ChatFeature client={new FixtureChatClient([endedWithTicket])} />)
    expect(await screen.findByRole('status')).toHaveTextContent('Case reference: DEMO-1234.')
  })

  it('shows a retryable error, not a stack trace, when the client rejects', async () => {
    const failing = {
      start: () => Promise.reject(new Error('network is down')),
      sendTurn: () => Promise.reject(new Error('unused')),
    }
    render(<ChatFeature client={failing} />)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The conversation could not start. Please try again.',
    )
    expect(screen.queryByText('network is down')).not.toBeInTheDocument()
  })

  it('shows a retryable error mid-conversation, alongside the messages so far', async () => {
    const user = userEvent.setup()
    const client: ChatClient = {
      start: () => new FixtureChatClient(FILE_DISPUTE_EN).start(),
      sendTurn: () => Promise.reject(new Error('network is down')),
    }
    render(<ChatFeature client={client} />)
    await screen.findByText('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your last message could not be sent. Please try again.',
    )
    expect(screen.getByText('Hi! Which transaction would you like to dispute?')).toBeInTheDocument()
    expect(screen.queryByText('network is down')).not.toBeInTheDocument()
  })

  it('disables the send button while a message is in flight', async () => {
    const user = userEvent.setup()
    renderChat()
    await screen.findByText('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'first message')
    const sendButton = screen.getByRole('button', { name: 'Send' })
    await user.click(sendButton)
    // The reply resolves on a microtask, so the disabled state is visible for at least one tick.
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await screen.findByText('I found one transaction. Is this the one?')
  })

  it('has no automatically detectable accessibility violations mid-conversation', async () => {
    const user = userEvent.setup()
    const { container } = renderChat()
    await screen.findByText('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('button', { name: /Tienda Sol/ })
    expect(await axe(container)).toHaveNoViolations()
  })
})
