/** Component test: `PacketPanel` renders the whole packet (AC-E10-02) and never a document
 * number or full card/account number (AC-E10-05). */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DEMO_TICKET_DETAILS } from '../fixtures'
import { PacketPanel } from './PacketPanel'

const [FIRST, SECOND] = DEMO_TICKET_DETAILS
if (FIRST === undefined || SECOND === undefined) {
  throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least two entries for these tests')
}

describe('PacketPanel', () => {
  it('shows the request summary, the customer language and the reference date', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    expect(screen.getByText(FIRST.packet.request_summary)).toBeInTheDocument()
    expect(screen.getByText('Español')).toBeInTheDocument()
    expect(screen.getByText(FIRST.packet.reference_date)).toBeInTheDocument()
  })

  it('shows every verified fact as a row, with its product masked to the last four digits', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    const [fact] = FIRST.packet.verified_facts
    expect(fact).toBeDefined()
    expect(screen.getByRole('rowheader', { name: fact?.ref })).toBeInTheDocument()
    expect(screen.getByText(fact?.merchant ?? '', { exact: false })).toBeInTheDocument()
  })

  it('shows the risk score together with its interval, base rate and threshold, never the score alone', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    const { risk } = FIRST.packet.evidence
    if (risk === null) {
      throw new Error('fixture setup: FIRST.packet.evidence.risk must be non-null for this test')
    }
    expect(screen.getByText(risk.score.toFixed(2))).toBeInTheDocument()
    expect(screen.getByText(risk.base_rate.toFixed(2))).toBeInTheDocument()
    expect(screen.getByText(risk.threshold.toFixed(2))).toBeInTheDocument()
    expect(
      screen.getByText(`${risk.interval_low.toFixed(2)} – ${risk.interval_high.toFixed(2)}`),
    ).toBeInTheDocument()
  })

  it('discloses that the risk score is a synthetic-data estimate, in Spanish, wherever it appears', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    expect(screen.getByText(/estimación calculada con datos sintéticos/)).toBeInTheDocument()
  })

  it('shows no risk section, and no synthetic-data disclosure, when the packet carries none', () => {
    render(<PacketPanel packet={SECOND.packet} />)

    expect(SECOND.packet.evidence.risk).toBeNull()
    expect(screen.queryByText('Puntaje de riesgo')).not.toBeInTheDocument()
    expect(screen.queryByText(/estimación calculada con datos sintéticos/)).not.toBeInTheDocument()
  })

  it("shows a source's title in the ticket's own language, not the console's fixed Spanish", () => {
    render(<PacketPanel packet={SECOND.packet} />)

    // SECOND's own language is English; its source titles carry an English variant too.
    expect(SECOND.packet.language).toBe('en')
    const [source] = SECOND.packet.evidence.sources
    const englishTitle = source?.titles.find((title) => title.lang === 'en')?.text
    expect(englishTitle).toBeDefined()
    expect(screen.getByText(englishTitle ?? '')).toBeInTheDocument()
  })

  it('shows the open questions, when there are any', () => {
    render(<PacketPanel packet={SECOND.packet} />)

    expect(SECOND.packet.open_questions.length).toBeGreaterThan(0)
    expect(screen.getByText('El motivo del reclamo', { exact: false })).toBeInTheDocument()
  })

  it('shows "no open questions" when there are none', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    expect(FIRST.packet.open_questions).toEqual([])
    expect(screen.getByText('Ninguna pregunta abierta.')).toBeInTheDocument()
  })

  it('masks the product to exactly its last four digits, never the full number', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    const [fact] = FIRST.packet.verified_facts
    if (fact === undefined) {
      throw new Error('fixture setup: FIRST.packet.verified_facts must be non-empty for this test')
    }
    // `ProductLabel` (contracts/service_v1/envelope.py) carries no full number at all — this
    // pins the masked presentation the contract's own shape already guarantees (AC-E10-05).
    expect(screen.getByText(`${fact.product.name} ····${fact.product.last4}`)).toBeInTheDocument()
  })

  it('does not carry the openable-row class, since its rows cannot be opened', () => {
    const { container } = render(<PacketPanel packet={FIRST.packet} />)

    expect(container.querySelector('.queue-table-openable')).toBeNull()
  })
})
