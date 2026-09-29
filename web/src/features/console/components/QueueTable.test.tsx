/** Component test: `QueueTable`'s rows, empty state and overdue flag. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
    ...overrides,
  }
}

describe('QueueTable', () => {
  it('shows a message instead of a table when there are no items', () => {
    render(<QueueTable items={[]} />)

    expect(screen.getByText('Ningún ticket coincide con este filtro.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders one row per item, with its reference, trigger, language, category and status', () => {
    render(<QueueTable items={[item()]} />)

    expect(screen.getByRole('cell', { name: 'Reporte de fraude' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'T-20260618-AAAAAAAA' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Español' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Reclamo de fraude' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Abierto' })).toBeInTheDocument()
  })

  it('shows an em dash for a ticket with no category', () => {
    render(<QueueTable items={[item({ category: null })]} />)

    expect(screen.getByRole('cell', { name: '—' })).toBeInTheDocument()
  })

  it('flags a ticket whose promised contact date has already passed', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-20', promised_contact_by: '2026-06-19' })]}
      />,
    )

    expect(screen.getByText('vencido', { exact: false })).toBeInTheDocument()
  })

  it('does not flag a ticket whose promised contact date has not passed yet', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-18', promised_contact_by: '2026-06-19' })]}
      />,
    )

    expect(screen.queryByText('vencido', { exact: false })).not.toBeInTheDocument()
  })

  it('does not flag a ticket due today, the boundary between not-yet and overdue', () => {
    render(
      <QueueTable
        items={[item({ reference_date: '2026-06-18', promised_contact_by: '2026-06-18' })]}
      />,
    )

    expect(screen.queryByText('vencido', { exact: false })).not.toBeInTheDocument()
  })
})
