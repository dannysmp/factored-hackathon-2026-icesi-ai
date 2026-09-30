/** Component test: the queue screen's states (AC-E10-18) and its accessibility. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { QueueClient } from './client'
import { FixtureQueueClient } from './client'
import type { QueueResponse } from './contracts'
import { DEMO_QUEUE, EMPTY_QUEUE } from './fixtures'
import { QueueScreen } from './QueueScreen'

const FAILING_CLIENT: QueueClient = {
  fetchQueue: () => Promise.reject(new Error('down')),
}

describe('QueueScreen', () => {
  it('shows the loading state before the first response arrives', () => {
    const client: QueueClient = { fetchQueue: () => new Promise(() => undefined) }
    render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('Cargando la cola')
  })

  it('shows the whole queue, priority tickets first, once it loads', async () => {
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)

    const rows = await screen.findAllByRole('row')
    // Header row, then one row per fixture item, in the fixture's own (priority-first) order.
    expect(rows).toHaveLength(DEMO_QUEUE.items.length + 1)
    const [firstItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()
    expect(rows[1]).toHaveTextContent(firstItem?.ticket_ref ?? '')
  })

  it('calls onSelectTicket when a ticket reference is clicked', async () => {
    const user = userEvent.setup()
    const onSelectTicket = vi.fn()
    render(
      <QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={onSelectTicket} />,
    )
    await screen.findAllByRole('row')
    const [firstItem] = DEMO_QUEUE.items
    expect(firstItem).toBeDefined()

    await user.click(screen.getByRole('button', { name: firstItem?.ticket_ref ?? '' }))

    expect(onSelectTicket).toHaveBeenCalledWith(firstItem?.ticket_ref)
  })

  it('shows the empty state when there are no open tickets at all', async () => {
    render(<QueueScreen client={new FixtureQueueClient(EMPTY_QUEUE)} onSelectTicket={vi.fn()} />)

    expect(await screen.findByText('No hay tickets abiertos en este momento.')).toBeInTheDocument()
  })

  it('narrows the table to the priority tab, then back to all', async () => {
    const user = userEvent.setup()
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.click(screen.getByRole('tab', { name: 'Fraude y pérdida de tarjeta' }))

    const priorityRows = screen.getAllByRole('row')
    expect(priorityRows).toHaveLength(DEMO_QUEUE.items.filter((item) => item.priority).length + 1)

    await user.click(screen.getByRole('tab', { name: 'Todos' }))
    expect(screen.getAllByRole('row')).toHaveLength(DEMO_QUEUE.items.length + 1)
  })

  it('narrows the table by language', async () => {
    const user = userEvent.setup()
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')

    const expectedCount = DEMO_QUEUE.items.filter((item) => item.language === 'pt').length
    expect(await screen.findAllByRole('row')).toHaveLength(expectedCount + 1)
  })

  it('shows an updating affordance and keeps the stale table while a filter refetch is in flight', async () => {
    const user = userEvent.setup()
    let callCount = 0
    let resolveSecond: (response: QueueResponse) => void = () => undefined
    const client: QueueClient = {
      fetchQueue: () => {
        callCount += 1
        if (callCount === 1) return Promise.resolve(DEMO_QUEUE)
        return new Promise((resolve) => {
          resolveSecond = resolve
        })
      },
    }
    render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')

    expect(screen.getByRole('status')).toHaveTextContent('Actualizando')
    // The previous response's table stays on screen, not blanked, while the refetch is pending.
    expect(screen.getAllByRole('row')).toHaveLength(DEMO_QUEUE.items.length + 1)

    resolveSecond({
      ...DEMO_QUEUE,
      items: DEMO_QUEUE.items.filter((item) => item.language === 'pt'),
    })

    await waitFor(() => {
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })
  })

  it('shows a retryable error when the queue cannot be loaded', async () => {
    render(<QueueScreen client={FAILING_CLIENT} onSelectTicket={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo cargar la cola')
    expect(screen.getByRole('button', { name: 'Intentar de nuevo' })).toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations once ready', async () => {
    const { container } = render(
      <QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />,
    )

    await screen.findAllByRole('row')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations while loading', async () => {
    const client: QueueClient = { fetchQueue: () => new Promise(() => undefined) }
    const { container } = render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)

    await screen.findByRole('status')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations while a filter refetch is in flight', async () => {
    const user = userEvent.setup()
    let callCount = 0
    const client: QueueClient = {
      fetchQueue: () => {
        callCount += 1
        return callCount === 1 ? Promise.resolve(DEMO_QUEUE) : new Promise(() => undefined)
      },
    }
    const { container } = render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')
    await screen.findByRole('status')

    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations in the error state', async () => {
    const { container } = render(<QueueScreen client={FAILING_CLIENT} onSelectTicket={vi.fn()} />)

    await screen.findByRole('alert')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations when the queue is empty', async () => {
    const { container } = render(
      <QueueScreen client={new FixtureQueueClient(EMPTY_QUEUE)} onSelectTicket={vi.fn()} />,
    )

    await screen.findByText('No hay tickets abiertos en este momento.')
    expect(await axe(container)).toHaveNoViolations()
  })
})
