import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageList } from './MessageList'

describe('MessageList', () => {
  it('shows a purpose-built empty state when there are no messages yet', () => {
    render(<MessageList messages={[]} />)
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
})
