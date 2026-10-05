/** Component test: the queue can be driven from the keyboard alone. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FixtureQueueClient } from './client'
import { DEMO_QUEUE } from './fixtures'
import { QueueScreen } from './QueueScreen'

describe('QueueScreen keyboard traversal', () => {
  it('tabs from the language filter to the active view tab, then into the ticket list', async () => {
    const user = userEvent.setup()
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.tab()
    expect(screen.getByLabelText('Idioma')).toHaveFocus()

    // Only the selected tab is in the tab order; the other two are reached with the arrow keys.
    await user.tab()
    expect(screen.getByRole('tab', { name: /^Todos \(\d+\)$/ })).toHaveFocus()

    const [firstItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()
    // The tab panel takes the next stop; the first ticket reference follows it.
    await user.tab()
    expect(screen.getByRole('tabpanel')).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: firstItem?.ticket_ref ?? '' })).toHaveFocus()
  })

  it('moves between the view tabs with the arrow keys and narrows the table', async () => {
    const user = userEvent.setup()
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    screen.getByRole('tab', { name: /^Todos \(\d+\)$/ }).focus()
    await user.keyboard('{ArrowRight}')

    const priorityTab = screen.getByRole('tab', { name: /^Fraude y pérdida de tarjeta \(\d+\)$/ })
    expect(priorityTab).toHaveFocus()
    expect(priorityTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('row')).toHaveLength(
      DEMO_QUEUE.items.filter((item) => item.priority).length + 1,
    )

    await user.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: /^Todos \(\d+\)$/ })).toHaveFocus()
    expect(screen.getAllByRole('row')).toHaveLength(DEMO_QUEUE.items.length + 1)
  })

  it('opens a ticket with Enter or Space on its reference', async () => {
    const user = userEvent.setup()
    const onSelectTicket = vi.fn()
    render(
      <QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={onSelectTicket} />,
    )
    await screen.findAllByRole('row')
    const [firstItem, secondItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()
    expect(secondItem).toBeDefined()

    screen.getByRole('button', { name: firstItem?.ticket_ref ?? '' }).focus()
    await user.keyboard('{Enter}')
    expect(onSelectTicket).toHaveBeenLastCalledWith(firstItem?.ticket_ref)

    await user.tab()
    expect(screen.getByRole('button', { name: secondItem?.ticket_ref ?? '' })).toHaveFocus()
    await user.keyboard(' ')
    expect(onSelectTicket).toHaveBeenLastCalledWith(secondItem?.ticket_ref)
  })
})
