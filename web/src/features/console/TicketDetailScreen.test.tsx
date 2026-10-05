/**
 * Component test: the ticket-detail screen renders its loading, ready (packet and timeline tabs),
 * not-found and error states, offers a back control, and has no automatically detectable
 * accessibility violations when ready or failed.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it, vi } from 'vitest'
import type { TicketDetailClient } from './ticketDetailClient'
import { FixtureTicketDetailClient } from './ticketDetailClient'
import { DEMO_TICKET_DETAILS } from './fixtures'
import { REQUEST_SUMMARY_LABELS } from './labels'
import { TicketDetailScreen } from './TicketDetailScreen'

const [FIRST] = DEMO_TICKET_DETAILS
if (FIRST === undefined) {
  throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least one entry for these tests')
}
const TICKET_REF = FIRST.item.ticket_ref

const FAILING_CLIENT: TicketDetailClient = {
  fetchTicketDetail: () => Promise.reject(new Error('down')),
}

describe('TicketDetailScreen', () => {
  it('shows the loading state before the first response arrives', () => {
    const client: TicketDetailClient = { fetchTicketDetail: () => new Promise(() => undefined) }
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)

    expect(screen.getByRole('status')).toHaveTextContent('Cargando el caso')
  })

  it('shows the packet by default, with a tab to switch to the timeline', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)

    expect(
      await screen.findByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger]),
    ).toBeInTheDocument()
    expect(screen.queryByText('Cronología de auditoría')).not.toBeInTheDocument()
  })

  it('switches to the timeline tab and shows its entries', async () => {
    const user = userEvent.setup()
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)
    await screen.findByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger])

    await user.click(screen.getByRole('tab', { name: 'Cronología' }))

    const [firstEntry] = FIRST.timeline
    expect(firstEntry).toBeDefined()
    expect(screen.getByText(firstEntry?.trace_id ?? '')).toBeInTheDocument()
  })

  it('calls onBack when the back control is used', async () => {
    const user = userEvent.setup()
    const onBack = vi.fn()
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={onBack} />)
    await screen.findByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger])

    await user.click(screen.getByRole('button', { name: 'Volver a la cola' }))

    expect(onBack).toHaveBeenCalled()
  })

  it('marks the back control so its label lines up with the content below it', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)
    await screen.findByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger])

    expect(screen.getByRole('button', { name: 'Volver a la cola' })).toHaveClass('ticket-back')
  })

  it('shows a message and a back control when the ticket does not resolve', async () => {
    const user = userEvent.setup()
    const onBack = vi.fn()
    const client: TicketDetailClient = { fetchTicketDetail: () => Promise.resolve(null) }
    render(<TicketDetailScreen client={client} ticketRef="T-GONE" onBack={onBack} />)

    expect(await screen.findByText('Este caso ya no está disponible.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Volver a la cola' }))
    expect(onBack).toHaveBeenCalled()
  })

  it('shows a retryable error when the ticket cannot be loaded', async () => {
    render(<TicketDetailScreen client={FAILING_CLIENT} ticketRef={TICKET_REF} onBack={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo cargar el caso')
    expect(screen.getByRole('button', { name: 'Intentar de nuevo' })).toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations when ready', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    const { container } = render(
      <TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />,
    )

    await screen.findByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger])
    expect(await axe(container)).toHaveNoViolations()
  })

  it('has no automatically detectable accessibility violations in the error state', async () => {
    const { container } = render(
      <TicketDetailScreen client={FAILING_CLIENT} ticketRef={TICKET_REF} onBack={vi.fn()} />,
    )

    await screen.findByRole('alert')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('moves focus to the case heading once the case is ready', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)

    const heading = await screen.findByRole('heading', { level: 2, name: `Caso ${TICKET_REF}` })

    expect(heading).toHaveFocus()
  })

  it('does not take focus from a control the person already holds', async () => {
    render(<input aria-label="otro control" />)
    screen.getByLabelText('otro control').focus()
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    render(<TicketDetailScreen client={client} ticketRef={TICKET_REF} onBack={vi.fn()} />)

    const heading = await screen.findByRole('heading', { level: 2, name: `Caso ${TICKET_REF}` })

    expect(heading).not.toHaveFocus()
    expect(screen.getByLabelText('otro control')).toHaveFocus()
  })
})
