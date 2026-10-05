/** Component test: the queue screen's states (AC-E10-18) and its accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { QueueClient } from './client'
import { FixtureQueueClient } from './client'
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

    expect(await screen.findByText('No hay casos abiertos en este momento.')).toBeInTheDocument()
  })

  it('keeps the filters when a language has no cases, so the filter can be undone', async () => {
    const user = userEvent.setup()
    const onlySpanish = {
      ...DEMO_QUEUE,
      items: DEMO_QUEUE.items.filter((i) => i.language === 'es'),
    }
    render(<QueueScreen client={new FixtureQueueClient(onlySpanish)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'en')

    expect(await screen.findByText('Ningún caso coincide con este filtro.')).toBeInTheDocument()
    expect(screen.queryByText('No hay casos abiertos en este momento.')).not.toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Idioma'), 'all')

    expect(await screen.findAllByRole('row')).toHaveLength(onlySpanish.items.length + 1)
  })

  it('narrows the table to the priority tab, then back to all', async () => {
    const user = userEvent.setup()
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.click(screen.getByRole('tab', { name: /^Fraude y pérdida de tarjeta \(\d+\)$/ }))

    const priorityRows = screen.getAllByRole('row')
    expect(priorityRows).toHaveLength(DEMO_QUEUE.items.filter((item) => item.priority).length + 1)

    await user.click(screen.getByRole('tab', { name: /^Todos \(\d+\)$/ }))
    expect(screen.getAllByRole('row')).toHaveLength(DEMO_QUEUE.items.length + 1)
  })

  it('shows how many cases each view holds in its tab', async () => {
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    const priority = DEMO_QUEUE.items.filter((item) => item.priority).length
    const all = DEMO_QUEUE.items.length
    expect(screen.getByRole('tab', { name: `Todos (${String(all)})` })).toBeInTheDocument()
    expect(
      screen.getByRole('tab', { name: `Fraude y pérdida de tarjeta (${String(priority)})` }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('tab', { name: `Otros (${String(all - priority)})` }),
    ).toBeInTheDocument()
  })

  it('writes the reference date the way an agent reads a date', async () => {
    render(<QueueScreen client={new FixtureQueueClient(DEMO_QUEUE)} onSelectTicket={vi.fn()} />)

    expect(
      await screen.findByText('Fecha de referencia de los datos: 18 jun 2026.'),
    ).toBeInTheDocument()
  })

  it('shows an updating affordance over the still-visible table during a filter refetch', async () => {
    const user = userEvent.setup()
    let callCount = 0
    const client: QueueClient = {
      fetchQueue: (filters) => {
        callCount += 1
        // The first call (initial load) resolves; the second (the language change below) never
        // does, freezing the screen mid-refetch so the still-stale table and the affordance can
        // both be asserted on at once.
        if (callCount === 1) {
          return new FixtureQueueClient(DEMO_QUEUE).fetchQueue(filters)
        }
        return new Promise(() => undefined)
      },
    }
    render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')

    expect(screen.getByRole('status')).toHaveTextContent('Actualizando')
    // The table itself is still the last-loaded (unfiltered) rows, not cleared or replaced: the
    // language filter's own effect on the row count only lands once the refetch resolves, which
    // this test's second call deliberately never does.
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

  it('has no automatically detectable accessibility violations when a language has no cases', async () => {
    const user = userEvent.setup()
    const onlySpanish = {
      ...DEMO_QUEUE,
      items: DEMO_QUEUE.items.filter((i) => i.language === 'es'),
    }
    const { container } = render(
      <QueueScreen client={new FixtureQueueClient(onlySpanish)} onSelectTicket={vi.fn()} />,
    )
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'en')

    await screen.findByText('Ningún caso coincide con este filtro.')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations while loading', async () => {
    const client: QueueClient = { fetchQueue: () => new Promise(() => undefined) }
    const { container } = render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)

    await screen.findByRole('status')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations while updating', async () => {
    const user = userEvent.setup()
    let callCount = 0
    const client: QueueClient = {
      fetchQueue: (filters) => {
        callCount += 1
        if (callCount === 1) {
          return new FixtureQueueClient(DEMO_QUEUE).fetchQueue(filters)
        }
        return new Promise(() => undefined)
      },
    }
    const { container } = render(<QueueScreen client={client} onSelectTicket={vi.fn()} />)
    await screen.findAllByRole('row')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')
    await screen.findByText('Actualizando…')

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

    await screen.findByText('No hay casos abiertos en este momento.')
    expect(await axe(container)).toHaveNoViolations()
  })
})
