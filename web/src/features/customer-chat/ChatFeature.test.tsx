/** Component test: the whole scripted conversation, its four async states, and accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import { ChatFeature } from './ChatFeature'
import type { ChatClient } from './client'
import { FixtureChatClient } from './client'
import { FILE_DISPUTE_EN } from './fixtures'
import { FILE_DISPUTE_ES } from './fixtures.es'
import { findMessage } from './findMessage'

function renderChat(): ReturnType<typeof render> {
  return render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN)} lang="en" />)
}

/** The hidden region that reads new replies aloud, as opposed to the typing row's status. */
function announcement(): HTMLElement {
  const region = screen
    .getAllByRole('status')
    .find((element) => element.getAttribute('aria-live') === 'polite')
  if (region === undefined) throw new Error('expected the announcement region to be on the page')
  return region
}

/** The visible closing line, as opposed to the hidden announcement that repeats it. */
function visibleEndedLine(): Element {
  const line = document.querySelector('p[class*="ended"]')
  if (line === null) throw new Error('expected the visible ended line to be on the page')
  return line
}

describe('ChatFeature', () => {
  it('shows only its own loading text while starting, never MessageList’s empty state too', () => {
    const pending = (): Promise<never> =>
      new Promise(() => {
        // never resolves: simulates the chat still starting
      })
    const client: ChatClient = { start: pending, sendTurn: pending }
    render(<ChatFeature client={client} lang="en" />)
    expect(screen.getByText('Starting the conversation…')).toBeInTheDocument()
    expect(screen.queryByText('No messages yet.')).not.toBeInTheDocument()
  })

  it('prefers the arriving turn’s own lang over the initial prop once one arrives', async () => {
    // The initial prop says English (the persona's language before any turn exists); the
    // fixture's own turns say Spanish (the server's grounded value) — the chrome must follow
    // the turn, not the stale initial guess.
    render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_ES)} lang="en" />)
    expect(await screen.findByRole('button', { name: 'Enviar' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument()
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

    await findMessage('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const choice = await screen.findByRole('button', {
      name: '1. MXN 250.00 at Tienda Sol on 12 June 2026',
    })
    await user.click(choice)

    await findMessage(/what is the reason for the dispute/i)
    await user.type(screen.getByLabelText('Your message'), 'unrecognized charge')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const confirm = await screen.findByRole('button', { name: 'Confirm' })
    await user.click(confirm)

    expect(await findMessage(/case DEMO-1234/)).toBeInTheDocument()
    expect(
      screen.getByText((_content, element) => element?.textContent === 'You: unrecognized charge'),
    ).toBeInTheDocument()
  })

  it('disables the form and shows a distinct ended state once the assistant ends the session', async () => {
    // Start straight from the last, session-ending turn.
    render(<ChatFeature client={new FixtureChatClient(FILE_DISPUTE_EN.slice(-1))} lang="en" />)
    await findMessage('Thanks for reaching out. Have a good day!')
    expect(screen.queryByLabelText('Your message')).not.toBeInTheDocument()
    expect(visibleEndedLine()).toHaveTextContent('This conversation has ended.')
  })

  it('shows the case reference on the ended state when the last turn carries one', async () => {
    const [lastTurn] = FILE_DISPUTE_EN.slice(-1)
    if (lastTurn === undefined) {
      throw new Error('fixture FILE_DISPUTE_EN must have at least one turn')
    }
    const endedWithTicket = { ...lastTurn, handoff_ticket: 'DEMO-1234' }
    render(<ChatFeature client={new FixtureChatClient([endedWithTicket])} lang="en" />)
    await findMessage('Thanks for reaching out. Have a good day!')
    expect(visibleEndedLine()).toHaveTextContent(
      'This conversation has ended. Case reference: DEMO-1234.',
    )
  })

  it('announces the closing line and the case reference together with the last assistant reply', async () => {
    const [lastTurn] = FILE_DISPUTE_EN.slice(-1)
    if (lastTurn === undefined) {
      throw new Error('fixture FILE_DISPUTE_EN must have at least one turn')
    }
    const endedWithTicket = { ...lastTurn, handoff_ticket: 'DEMO-1234' }
    render(<ChatFeature client={new FixtureChatClient([endedWithTicket])} lang="en" />)
    await findMessage('Thanks for reaching out. Have a good day!')

    expect(announcement()).toHaveTextContent(
      'Thanks for reaching out. Have a good day! This conversation has ended. Case reference: DEMO-1234.',
    )
  })

  it('announces the new assistant reply and never reads the customer’s own message back', async () => {
    const user = userEvent.setup()
    let release: () => void = () => undefined
    const fixture = new FixtureChatClient(FILE_DISPUTE_EN)
    const client: ChatClient = {
      start: () => fixture.start(),
      sendTurn: () =>
        new Promise((resolve) => {
          release = () => {
            resolve(fixture.sendTurn())
          }
        }),
    }
    render(<ChatFeature client={client} lang="en" />)
    await findMessage('Hi! Which transaction would you like to dispute?')
    expect(announcement()).toHaveTextContent('Hi! Which transaction would you like to dispute?')

    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(announcement()).not.toHaveTextContent('the Tienda Sol one')
    expect(announcement()).toHaveTextContent('Hi! Which transaction would you like to dispute?')

    release()
    await findMessage('I found one transaction. Is this the one?')
    expect(announcement()).toHaveTextContent('I found one transaction. Is this the one?')
    expect(announcement()).not.toHaveTextContent('the Tienda Sol one')
  })

  it('tells the page which language the conversation is in, so its title and document language follow', async () => {
    const onLanguageChange = vi.fn()
    render(
      <ChatFeature
        client={new FixtureChatClient(FILE_DISPUTE_ES)}
        lang="en"
        onLanguageChange={onLanguageChange}
      />,
    )
    await screen.findByRole('button', { name: 'Enviar' })

    expect(onLanguageChange).toHaveBeenCalledWith('en')
    expect(onLanguageChange).toHaveBeenLastCalledWith('es')
  })

  it('shows a retryable error, not a stack trace, when the client rejects', async () => {
    const failing = {
      start: () => Promise.reject(new Error('network is down')),
      sendTurn: () => Promise.reject(new Error('unused')),
    }
    render(<ChatFeature client={failing} lang="en" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The conversation could not start.')
    expect(screen.queryByText('network is down')).not.toBeInTheDocument()
  })

  it('shows a retryable error mid-conversation, alongside the messages so far', async () => {
    const user = userEvent.setup()
    const client: ChatClient = {
      start: () => new FixtureChatClient(FILE_DISPUTE_EN).start(),
      sendTurn: () => Promise.reject(new Error('network is down')),
    }
    render(<ChatFeature client={client} lang="en" />)
    await findMessage('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your last message could not be sent.',
    )
    expect(
      await findMessage('Hi! Which transaction would you like to dispute?'),
    ).toBeInTheDocument()
    expect(screen.queryByText('network is down')).not.toBeInTheDocument()
  })

  it('disables the send button while a message is in flight', async () => {
    const user = userEvent.setup()
    renderChat()
    await findMessage('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'first message')
    const sendButton = screen.getByRole('button', { name: 'Send' })
    await user.click(sendButton)
    // The reply resolves on a microtask, so the disabled state is visible for at least one tick.
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await findMessage('I found one transaction. Is this the one?')
  })

  it('has no automatically detectable accessibility violations mid-conversation', async () => {
    const user = userEvent.setup()
    const { container } = renderChat()
    await findMessage('Hi! Which transaction would you like to dispute?')
    await user.type(screen.getByLabelText('Your message'), 'the Tienda Sol one')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('button', { name: /Tienda Sol/ })
    expect(await axe(container)).toHaveNoViolations()
  })
})
