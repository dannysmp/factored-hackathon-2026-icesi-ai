/**
 * Component test: `QueueTable` writes one row per case with its reference, trigger, language,
 * category, status, dates and age; marks priority and overdue cases; and reports the selected
 * ticket reference. The rows show no document number.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { QueueItem } from '../contracts'
import { QueueTable } from './QueueTable'

function item(overrides: Partial<QueueItem> = {}): QueueItem {
  return {
    ticket_ref: 'T-20260618-AAAAAAAA',
    trigger: 'fraud_report',
    language: 'es',
    category: 'fraud_claim',
    status: 'open',
    created_at: '2026-06-18T14:05:00Z',
    reference_date: '2026-06-18',
    promised_contact_by: '2026-06-19',
    age_days: 0,
    priority: true,
    claimed_by: null,
    ...overrides,
  }
}

describe('QueueTable', () => {
  it('shows a message instead of a table when there are no items', () => {
    render(<QueueTable items={[]} onSelectTicket={vi.fn()} />)

    expect(screen.getByText('Ningún caso coincide con este filtro.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders one row per item, with its reference, trigger, language, category and status', () => {
    render(<QueueTable items={[item()]} onSelectTicket={vi.fn()} />)

    expect(screen.getByRole('cell', { name: 'Reporte de fraude' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: /^T-20260618-AAAAAAAA/ })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Español' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Reclamo de fraude' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Abierto' })).toBeInTheDocument()
  })

  it('writes the dates and the age the way an agent reads them', () => {
    render(
      <QueueTable
        items={[item({ age_days: 3, promised_contact_by: '2026-06-19' })]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.getByRole('cell', { name: '3 días' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '19 jun 2026' })).toBeInTheDocument()
  })

  it('writes an age of zero as today and of one as one day', () => {
    render(
      <QueueTable
        items={[
          item({ ticket_ref: 'T-20260618-AAAAAAAA', age_days: 0 }),
          item({ ticket_ref: 'T-20260617-BBBBBBBB', age_days: 1 }),
        ]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.getByRole('cell', { name: 'Hoy' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '1 día' })).toBeInTheDocument()
  })

  it('marks a priority case with the word in its reference cell, and an ordinary one without it', () => {
    render(
      <QueueTable
        items={[
          item({ ticket_ref: 'T-20260618-AAAAAAAA', priority: true }),
          item({ ticket_ref: 'T-20260618-BBBBBBBB', priority: false }),
        ]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.getByRole('rowheader', { name: /T-20260618-AAAAAAAA/ })).toHaveTextContent(
      'Prioritario',
    )
    expect(screen.getByRole('rowheader', { name: /T-20260618-BBBBBBBB/ })).not.toHaveTextContent(
      'Prioritario',
    )
  })

  it('puts the priority bar on a priority row and on no other, and gives the mark an icon', () => {
    render(
      <QueueTable
        items={[
          item({ ticket_ref: 'T-20260618-AAAAAAAA', priority: true }),
          item({ ticket_ref: 'T-20260618-BBBBBBBB', priority: false }),
        ]}
        onSelectTicket={vi.fn()}
      />,
    )

    const priorityRow = screen.getByRole('rowheader', { name: /T-20260618-AAAAAAAA/ }).closest('tr')
    const ordinaryRow = screen.getByRole('rowheader', { name: /T-20260618-BBBBBBBB/ }).closest('tr')
    expect(priorityRow).toHaveClass('queue-row-priority')
    expect(ordinaryRow).not.toHaveClass('queue-row-priority')
    expect(
      priorityRow?.querySelector('.queue-priority-badge svg[aria-hidden="true"]'),
    ).not.toBeNull()
    expect(ordinaryRow?.querySelector('.queue-priority-badge')).toBeNull()
  })

  it('calls onSelectTicket with the ticket_ref when its reference is clicked', async () => {
    const user = userEvent.setup()
    const onSelectTicket = vi.fn()
    render(<QueueTable items={[item()]} onSelectTicket={onSelectTicket} />)

    await user.click(screen.getByRole('button', { name: 'T-20260618-AAAAAAAA' }))

    expect(onSelectTicket).toHaveBeenCalledWith('T-20260618-AAAAAAAA')
  })

  it('shows an em dash for a ticket with no category', () => {
    render(<QueueTable items={[item({ category: null })]} onSelectTicket={vi.fn()} />)

    expect(screen.getByRole('cell', { name: '—' })).toBeInTheDocument()
  })

  it('flags a ticket whose promised contact date has already passed', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-20', promised_contact_by: '2026-06-19' })]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.getByText('vencido', { exact: false })).toBeInTheDocument()
  })

  it('does not flag a ticket whose promised contact date has not passed yet', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-18', promised_contact_by: '2026-06-19' })]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.queryByText('vencido', { exact: false })).not.toBeInTheDocument()
  })

  it('does not flag a ticket due today, the boundary between not-yet and overdue', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-18', promised_contact_by: '2026-06-18' })]}
        onSelectTicket={vi.fn()}
      />,
    )

    expect(screen.queryByText('vencido', { exact: false })).not.toBeInTheDocument()
  })

  it('marks its table wrapper as the one whose rows can be opened, so rows highlight on hover', () => {
    const { container } = render(<QueueTable items={[item()]} onSelectTicket={vi.fn()} />)

    expect(container.querySelector('.queue-table-openable')).not.toBeNull()
  })
})
