/**
 * Component test: `MessageList` labels each message by sender, marks unsent ones, shows the typing
 * row politely, scrolls only to genuinely new content, and has no accessibility violations.
 */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Message } from '../useConversation'
import { MessageList } from './MessageList'

describe('MessageList', () => {
  it('shows a purpose-built empty state when there are no messages yet', () => {
    render(<MessageList messages={[]} lang="en" pending={false} />)
    expect(screen.getByText('No messages yet.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('renders each message labeled by its sender once there are some', () => {
    render(
      <MessageList
        messages={[
          { id: 'a', from: 'assistant', text: 'Hi there' },
          { id: 'b', from: 'customer', text: 'Hello' },
        ]}
        lang="en"
        pending={false}
      />,
    )
    expect(screen.queryByText('No messages yet.')).not.toBeInTheDocument()
    expect(
      screen.getByText((_content, element) => element?.textContent === 'Assistant: Hi there'),
    ).toBeInTheDocument()
    expect(
      screen.getByText((_content, element) => element?.textContent === 'You: Hello'),
    ).toBeInTheDocument()
  })

  it('reads its copy from the catalog matching lang, not a hardcoded English string', () => {
    render(<MessageList messages={[]} lang="es" pending={false} />)
    expect(screen.getByText('Aún no hay mensajes.')).toBeInTheDocument()
    expect(screen.queryByText('No messages yet.')).not.toBeInTheDocument()
  })

  describe('bubbles', () => {
    const conversation: Message[] = [
      { id: 'a', from: 'assistant', text: 'Hi there' },
      { id: 'b', from: 'customer', text: 'Hello' },
    ]

    it('styles the customer and the assistant differently', () => {
      render(<MessageList messages={conversation} lang="en" pending={false} />)
      const assistant = screen.getByText('Hi there').closest('li')
      const customer = screen.getByText('Hello').closest('li')

      expect(assistant?.className).toMatch(/assistant/)
      expect(assistant?.className).not.toMatch(/customer/)
      expect(customer?.className).toMatch(/customer/)
      expect(customer?.className).not.toMatch(/assistant/)
    })

    it('marks a message that was not sent, in words as well as styling', () => {
      render(
        <MessageList
          messages={[...conversation, { id: 'c', from: 'customer', text: 'Again', failed: true }]}
          lang="en"
          pending={false}
        />,
      )
      const failed = screen.getByText('Again').closest('li')

      expect(failed?.className).toMatch(/failed/)
      expect(failed).toHaveTextContent('Not sent')
      expect(screen.getAllByText('Not sent')).toHaveLength(1)
    })
  })

  describe('awaiting a reply', () => {
    it('shows a typing row in a status region while pending, and nothing when not', () => {
      const messages: Message[] = [{ id: 'a', from: 'customer', text: 'Hello' }]
      const { rerender } = render(<MessageList messages={messages} lang="en" pending={false} />)
      const region = screen.getByRole('status')
      expect(region).toBeEmptyDOMElement()

      rerender(<MessageList messages={messages} lang="en" pending />)
      expect(screen.getByRole('status')).toBe(region)
      expect(region).toHaveTextContent('The assistant is typing…')
    })

    it('hides the animated dots from assistive technology, leaving only the words', () => {
      const { container } = render(
        <MessageList messages={[{ id: 'a', from: 'customer', text: 'Hello' }]} lang="en" pending />,
      )

      const dots = container.querySelector('[aria-hidden="true"]')
      expect(dots).not.toBeNull()
      expect(dots?.children).toHaveLength(3)
      expect(screen.getByRole('status')).toHaveTextContent(/^The assistant is typing…$/)
    })

    it('says so in the language of the conversation', () => {
      render(
        <MessageList messages={[{ id: 'a', from: 'customer', text: 'Hola' }]} lang="es" pending />,
      )
      expect(screen.getByRole('status')).toHaveTextContent('El asistente está escribiendo…')
    })

    it('has no automatically detectable accessibility violations while typing', async () => {
      const { container } = render(
        <MessageList messages={[{ id: 'a', from: 'customer', text: 'Hello' }]} lang="pt" pending />,
      )
      expect(await axe(container)).toHaveNoViolations()
    })
  })

  describe('scrolling to the newest content', () => {
    const scroll = vi.fn<(options?: ScrollIntoViewOptions) => void>()
    beforeEach(() => {
      Element.prototype.scrollIntoView = scroll
      scroll.mockClear()
    })
    const greeting: Message[] = [{ id: 'a', from: 'assistant', text: 'Hi there' }]

    it('does not scroll for the messages already there when it first renders', () => {
      scroll.mockClear()
      render(<MessageList messages={greeting} lang="en" pending={false} />)
      expect(scroll).not.toHaveBeenCalled()
    })

    it('brings a new reply into view from its first line', () => {
      const { rerender } = render(<MessageList messages={greeting} lang="en" pending={false} />)
      scroll.mockClear()

      rerender(
        <MessageList
          messages={[...greeting, { id: 'b', from: 'assistant', text: 'Found it' }]}
          lang="en"
          pending={false}
        />,
      )

      expect(scroll).toHaveBeenCalledTimes(1)
      expect(scroll).toHaveBeenCalledWith({ block: 'start' })
      expect(scroll.mock.contexts.at(-1)).toBe(screen.getByText('Found it').closest('li'))
    })

    it('brings the typing row into view when the customer sends a message', () => {
      const { rerender } = render(<MessageList messages={greeting} lang="en" pending={false} />)
      scroll.mockClear()

      rerender(
        <MessageList
          messages={[...greeting, { id: 'b', from: 'customer', text: 'This one' }]}
          lang="en"
          pending
        />,
      )

      expect(scroll).toHaveBeenCalledTimes(1)
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
      expect(scroll.mock.contexts.at(-1)).toBe(screen.getByRole('status'))
    })

    it('keeps a customer message at the nearest edge when no reply is pending', () => {
      const { rerender } = render(<MessageList messages={greeting} lang="en" pending={false} />)
      scroll.mockClear()

      rerender(
        <MessageList
          messages={[...greeting, { id: 'b', from: 'customer', text: 'This one' }]}
          lang="en"
          pending={false}
        />,
      )

      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
      expect(scroll.mock.contexts.at(-1)).toBe(screen.getByText('This one').closest('li'))
    })

    it('does not scroll again when nothing new arrived', () => {
      const { rerender } = render(<MessageList messages={greeting} lang="en" pending={false} />)
      scroll.mockClear()

      rerender(<MessageList messages={[...greeting]} lang="en" pending={false} />)

      expect(scroll).not.toHaveBeenCalled()
    })
  })
})
