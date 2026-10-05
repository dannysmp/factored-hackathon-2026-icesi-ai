/** Component test: `TimelinePanel` orders entries by the moment they occurred and shows no
 * message text, which `TimelineEntry`'s own shape guarantees structurally. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
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

  it('orders entries by the moment they occurred, regardless of the order they arrive in', () => {
    render(
      <TimelinePanel
        entries={[
          entry({ turn_id: 'turn-b', trace_id: 'trace-0001', occurred_at: '2026-06-18T14:05:00Z' }),
          entry({ turn_id: 'turn-a', trace_id: 'trace-0002', occurred_at: '2026-06-18T14:03:00Z' }),
        ]}
      />,
    )

    const rows = screen.getAllByRole('row')
    // The later trace identifier happened first, so it comes first.
    expect(rows[1]).toHaveTextContent('trace-0002')
    expect(rows[2]).toHaveTextContent('trace-0001')
  })

  it('compares moments, not their text, when entries are written with different offsets', () => {
    render(
      <TimelinePanel
        entries={[
          entry({
            turn_id: 'turn-b',
            trace_id: 'trace-b',
            occurred_at: '2026-06-18T10:30:00-05:00',
          }),
          entry({ turn_id: 'turn-a', trace_id: 'trace-a', occurred_at: '2026-06-18T15:00:00Z' }),
        ]}
      />,
    )

    // 10:30 at -05:00 is 15:30 UTC, after 15:00 UTC although it sorts before it as text.
    const rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('trace-a')
    expect(rows[2]).toHaveTextContent('trace-b')
  })

  it('keeps entries that share a trace identifier as separate rows, in the order they occurred', () => {
    render(
      <TimelinePanel
        entries={[
          entry({
            turn_id: 'turn-2',
            trace_id: 'trace-shared',
            occurred_at: '2026-06-18T14:05:00Z',
            intent: 'handoff',
          }),
          entry({
            turn_id: 'turn-1',
            trace_id: 'trace-shared',
            occurred_at: '2026-06-18T14:03:00Z',
          }),
        ]}
      />,
    )

    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent('18 jun 2026, 14:03 UTC')
    expect(rows[2]).toHaveTextContent('18 jun 2026, 14:05 UTC')
  })

  it('gives every row its own identity when entries share a trace identifier', () => {
    const problems = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      render(
        <TimelinePanel
          entries={[
            entry({ turn_id: 'turn-1', trace_id: 'trace-shared' }),
            entry({ turn_id: 'turn-2', trace_id: 'trace-shared' }),
          ]}
        />,
      )

      // React reports two siblings with the same key through the console.
      expect(problems).not.toHaveBeenCalled()
    } finally {
      problems.mockRestore()
    }
  })

  it('keeps entries recorded at the same instant in the order they arrived', () => {
    // Neither the turn nor the trace identifiers run in order (ascending or descending) across
    // the three arrivals, so no tie-break on either can reproduce the arrival order by accident.
    render(
      <TimelinePanel
        entries={[
          entry({ turn_id: 'turn-b', trace_id: 'trace-m' }),
          entry({ turn_id: 'turn-c', trace_id: 'trace-x' }),
          entry({ turn_id: 'turn-a', trace_id: 'trace-c' }),
        ]}
      />,
    )

    const rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('trace-m')
    expect(rows[2]).toHaveTextContent('trace-x')
    expect(rows[3]).toHaveTextContent('trace-c')
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
