/**
 * Component test: on a phone each table row is shown as a card whose values carry their column
 * title in `data-label`. Every value cell's label must equal the column title it sits under, so a
 * card never loses or mislabels a field.
 */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DEMO_QUEUE, DEMO_TICKET_DETAILS } from '../fixtures'
import { PacketPanel } from './PacketPanel'
import { QueueTable } from './QueueTable'
import { TimelinePanel } from './TimelinePanel'

const [FIRST] = DEMO_TICKET_DETAILS
if (FIRST === undefined) throw new Error('fixture setup: DEMO_TICKET_DETAILS must not be empty')

function expectCellsLabelledByColumn(container: HTMLElement): void {
  const table = container.querySelector('table')
  expect(table).not.toBeNull()
  expect(table?.classList.contains('stacked-table')).toBe(true)
  const titles = Array.from(table?.querySelectorAll('thead th') ?? []).map((th) => th.textContent)
  const rows = Array.from(table?.querySelectorAll('tbody tr') ?? [])
  expect(rows.length).toBeGreaterThan(0)
  for (const row of rows) {
    const cells = Array.from(row.querySelectorAll('td'))
    expect(cells).toHaveLength(titles.length - 1)
    cells.forEach((cell, index) => {
      expect(cell.getAttribute('data-label')).toBe(titles[index + 1])
    })
  }
}

describe('stacked tables', () => {
  it('labels every queue value with its column title', () => {
    const { container } = render(
      <QueueTable items={DEMO_QUEUE.items} onSelectTicket={() => undefined} />,
    )
    expectCellsLabelledByColumn(container)
  })

  it('keeps an overdue flag inside the value box of its date, not as a cell of its own', () => {
    const [first] = DEMO_QUEUE.items
    if (first === undefined) throw new Error('fixture setup: DEMO_QUEUE must not be empty')
    const overdue = { ...first, reference_date: '2026-06-20', promised_contact_by: '2026-06-18' }
    const { container } = render(<QueueTable items={[overdue]} onSelectTicket={() => undefined} />)

    const cell = container.querySelector('td[data-label="Contacto prometido para"]')
    const flag = container.querySelector('.queue-overdue-flag')
    expect(flag).not.toBeNull()
    expect(flag?.parentElement?.parentElement).toBe(cell)
    expect(cell?.children).toHaveLength(1)
  })

  it('labels every verified transaction value with its column title', () => {
    const { container } = render(<PacketPanel packet={FIRST.packet} />)
    expectCellsLabelledByColumn(container)
  })

  it('labels every audit entry value with its column title', () => {
    const { container } = render(<TimelinePanel entries={FIRST.timeline} />)
    expectCellsLabelledByColumn(container)
  })
})
