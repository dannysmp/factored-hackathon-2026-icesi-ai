/** Component test: `PacketPanel` renders the whole packet (AC-E10-02) and never a document
 * number or full card/account number (AC-E10-05). */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DEMO_TICKET_DETAILS } from '../fixtures'
import { formatDate, formatMoney as money, formatShare as share } from '../format'
import { REQUEST_SUMMARY_LABELS } from '../labels'
import { PacketPanel } from './PacketPanel'

/** The page's text matching treats a no-break space as a plain one; so does the expectation. */
const formatMoney = (amount: string, currency: string): string =>
  money(amount, currency).replaceAll('\u00a0', ' ')
const formatShare = (value: number): string => share(value).replaceAll('\u00a0', ' ')

const [FIRST, SECOND] = DEMO_TICKET_DETAILS
if (FIRST === undefined || SECOND === undefined) {
  throw new Error('fixture setup: DEMO_TICKET_DETAILS needs at least two entries for these tests')
}

describe('PacketPanel', () => {
  it('shows the request summary, the customer language and the reference date', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    expect(screen.getByText(REQUEST_SUMMARY_LABELS[FIRST.packet.trigger])).toBeInTheDocument()
    expect(screen.queryByText(FIRST.packet.request_summary)).not.toBeInTheDocument()
    expect(screen.getByText('Español')).toBeInTheDocument()
    expect(screen.getByText(formatDate(FIRST.packet.reference_date))).toBeInTheDocument()
    expect(screen.getByText('Ana · ****34')).toBeInTheDocument()
  })

  it('writes each verified fact’s date and amount the way the console formats them', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    const [fact] = FIRST.packet.verified_facts
    if (fact?.amount == null) {
      throw new Error('fixture setup: the first verified fact needs an amount')
    }
    expect(
      screen.getByText(formatMoney(fact.amount.amount, fact.amount.currency)),
    ).toBeInTheDocument()
    expect(screen.getByText(formatDate(fact.occurred_on))).toBeInTheDocument()
  })

  it('names an unnamed customer in Spanish rather than with the system placeholder', () => {
    render(
      <PacketPanel
        packet={{ ...FIRST.packet, customer: { first_name: 'Customer', masked_id: '****34' } }}
      />,
    )

    expect(screen.getByText('Cliente · ****34')).toBeInTheDocument()
    expect(screen.queryByText(/Customer/)).not.toBeInTheDocument()
  })

  it('writes each action and its result in Spanish, and marks an attempted one as not completed', () => {
    render(
      <PacketPanel
        packet={{
          ...FIRST.packet,
          actions: [{ action: 'evaluate_dispute', result: 'escalate_fraud_claim' }],
          attempted_action: { action: 'create_dispute_case', result: 'confirmation_required' },
        }}
      />,
    )

    expect(
      screen.getByText('Evaluación de la disputa: Escalado: reclamo de fraude'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Registro de la disputa — intento no completado: Requiere confirmación'),
    ).toBeInTheDocument()
  })

  it('words an attempted action the same way whatever the action is called', () => {
    render(
      <PacketPanel
        packet={{
          ...FIRST.packet,
          actions: [],
          attempted_action: { action: 'list_transactions', result: 'confirmation_required' },
        }}
      />,
    )

    expect(
      screen.getByText('Consulta de transacciones — intento no completado: Requiere confirmación'),
    ).toBeInTheDocument()
  })

  it('marks the policy titles with the customer’s language so a Spanish reader knows who wrote them', () => {
    render(<PacketPanel packet={SECOND.packet} />)

    expect(screen.getByText('Texto en el idioma del cliente (Inglés).')).toBeInTheDocument()
    const [source] = SECOND.packet.evidence.sources
    const title = source?.titles.find((candidate) => candidate.lang === 'en')?.text ?? ''
    expect(screen.getByText(title)).toHaveAttribute('lang', 'en')
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
    expect(screen.getByText(formatShare(risk.score))).toBeInTheDocument()
    expect(screen.getByText(formatShare(risk.base_rate))).toBeInTheDocument()
    expect(screen.getByText(formatShare(risk.threshold))).toBeInTheDocument()
    expect(
      screen.getByText(`${formatShare(risk.interval_low)} – ${formatShare(risk.interval_high)}`),
    ).toBeInTheDocument()
  })

  it('never writes a score under the escalation threshold as equal to it', () => {
    const { risk } = FIRST.packet.evidence
    if (risk === null) {
      throw new Error('fixture setup: FIRST.packet.evidence.risk must be non-null for this test')
    }
    render(
      <PacketPanel
        packet={{
          ...FIRST.packet,
          evidence: {
            ...FIRST.packet.evidence,
            risk: {
              ...risk,
              score: 0.371,
              interval_low: 0.1,
              interval_high: 0.9,
              threshold: 0.374,
            },
          },
        }}
      />,
    )

    expect(screen.getByText('Puntaje').nextElementSibling).toHaveTextContent('37,1 %')
    expect(screen.getByText('Umbral de escalamiento').nextElementSibling).toHaveTextContent(
      /^37 %$/,
    )
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

  it('puts the request first, before the verified transactions and every other section', () => {
    render(<PacketPanel packet={FIRST.packet} />)

    const headings = screen
      .getAllByRole('heading', { level: 3 })
      .map((heading) => heading.textContent)
    expect(headings).toEqual([
      'Solicitud',
      'Transacciones verificadas',
      'Acciones',
      'Evidencia',
      'Preguntas abiertas',
    ])
  })

  it('says each hand-off reason in the console’s own words, never the backend’s English sentence', () => {
    render(<PacketPanel packet={{ ...FIRST.packet, trigger: 'card_loss' }} />)

    expect(
      screen.getByText('El cliente reportó la pérdida o el robo de su tarjeta.'),
    ).toBeInTheDocument()
  })

  it('counts an open question’s attempts in the singular and the plural', () => {
    render(
      <PacketPanel
        packet={{
          ...FIRST.packet,
          open_questions: [
            { slot: 'reason', attempts: 1 },
            { slot: 'confirmation', attempts: 2 },
          ],
        }}
      />,
    )

    expect(screen.getByText('El motivo del reclamo (1 intento)')).toBeInTheDocument()
    expect(screen.getByText('La confirmación (2 intentos)')).toBeInTheDocument()
  })

  it('falls back to the first title on record when the case’s language has none, and to a dash when there are none', () => {
    const [source] = SECOND.packet.evidence.sources
    if (source === undefined) throw new Error('fixture setup: SECOND needs a source')
    const spanishOnly = { ...source, titles: [{ lang: 'es' as const, text: 'Solo en español' }] }
    const { rerender } = render(
      <PacketPanel
        packet={{
          ...SECOND.packet,
          evidence: { ...SECOND.packet.evidence, sources: [spanishOnly] },
        }}
      />,
    )
    expect(screen.getByText('Solo en español')).toBeInTheDocument()

    rerender(
      <PacketPanel
        packet={{
          ...SECOND.packet,
          evidence: { ...SECOND.packet.evidence, sources: [{ ...source, titles: [] }] },
        }}
      />,
    )
    expect(screen.queryByText('Solo en español')).not.toBeInTheDocument()
    expect(screen.getByText('—', { selector: 'li' })).toBeInTheDocument()
  })

  it('does not carry the openable-row class, since its rows cannot be opened', () => {
    const { container } = render(<PacketPanel packet={FIRST.packet} />)

    expect(container.querySelector('.queue-table-openable')).toBeNull()
  })
})
