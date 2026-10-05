/** Component test: `TimelinePanel` orders entries by trace identifier (AC-E10-03) and shows no
 * message text (AC-E10-05, structurally guaranteed by `TimelineEntry`'s own shape). */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { TimelineEntry } from '../contracts'
import { TimelinePanel } from './TimelinePanel'

function entry(overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    occurred_at: '2026-06-18T14:03:00Z',
    trace_id: 'trace-0001',
    turn_id: 'turn-0001',
    intent: 'present_transactions',
    state_before: 'started',
    state_after: 'clarifying',
    render_mode: 'template',
    reason_code: null,
    policy_version: null,
    ...overrides,
  }
}

describe('TimelinePanel', () => {
  it('shows "no records" when the timeline is empty', () => {
    render(<TimelinePanel entries={[]} />)

    expect(screen.getByText('Ningún registro de auditoría.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('orders entries by trace identifier, regardless of the order they arrive in', () => {
    render(
      <TimelinePanel
        entries={[entry({ trace_id: 'trace-0002' }), entry({ trace_id: 'trace-0001' })]}
      />,
    )

    const rows = screen.getAllByRole('row')
    // Header row, then trace-0001 before trace-0002, regardless of input order.
    expect(rows[1]).toHaveTextContent('trace-0001')
    expect(rows[2]).toHaveTextContent('trace-0002')
  })

  it('shows the state transition and the reason code when there is one', () => {
    render(<TimelinePanel entries={[entry({ reason_code: 'escalate_fraud_claim' })]} />)

    expect(screen.getByText('Iniciada → En aclaración')).toBeInTheDocument()
    expect(screen.getByText('Escalado: reclamo de fraude')).toBeInTheDocument()
  })

  it('writes the moment of each entry as a date and time, as the row header', () => {
    render(<TimelinePanel entries={[entry({ occurred_at: '2026-06-18T14:03:00Z' })]} />)

    expect(screen.getByRole('rowheader', { name: '18 jun 2026, 14:03 UTC' })).toBeInTheDocument()
  })

  it('writes a stage this console does not know as plain words, never as an identifier', () => {
    render(
      <TimelinePanel
        entries={[entry({ state_before: 'started', state_after: 'some_new_state' })]}
      />,
    )

    expect(screen.getByText('Iniciada → Sin etiqueta (some new state)')).toBeInTheDocument()
  })

  it('shows an em dash when there is no reason code or policy version', () => {
    render(<TimelinePanel entries={[entry({ reason_code: null, policy_version: null })]} />)

    // Both the reason-code and the policy-version cells fall back to an em dash on this entry.
    expect(screen.getAllByRole('cell', { name: '—' })).toHaveLength(2)
  })

  it('does not carry the openable-row class, since its rows cannot be opened', () => {
    const { container } = render(<TimelinePanel entries={[entry()]} />)

    expect(container.querySelector('.queue-table-openable')).toBeNull()
  })
})
