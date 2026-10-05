/** Component test: the result card's three outcomes in each language, and its accessibility. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import type { Lang } from '../../../i18n/lang'
import { ResultCard } from './ResultCard'

const TITLES: Record<Lang, { filed: string; escalated: string; closed: string }> = {
  es: {
    filed: 'Disputa registrada',
    escalated: 'Un asesor revisará su solicitud',
    closed: 'Conversación finalizada',
  },
  pt: {
    filed: 'Contestação registrada',
    escalated: 'Um atendente vai analisar sua solicitação',
    closed: 'Conversa encerrada',
  },
  en: {
    filed: 'Dispute filed',
    escalated: 'A person will review your request',
    closed: 'Conversation ended',
  },
}

describe('ResultCard', () => {
  for (const lang of ['es', 'pt', 'en'] as const) {
    it(`names each outcome in ${lang} and shows the reference only when there is one`, () => {
      const { rerender } = render(
        <ResultCard lang={lang} caseNumber="D-2001" handoffTicket={null} />,
      )
      expect(screen.getByRole('region', { name: TITLES[lang].filed })).toHaveTextContent('D-2001')

      rerender(<ResultCard lang={lang} caseNumber={null} handoffTicket="DEMO-1234" />)
      expect(screen.getByRole('region', { name: TITLES[lang].escalated })).toHaveTextContent(
        'DEMO-1234',
      )

      rerender(<ResultCard lang={lang} caseNumber={null} handoffTicket={null} />)
      const closed = screen.getByRole('region', { name: TITLES[lang].closed })
      expect(closed.querySelector('p')).toBeNull()
    })
  }

  it('lists the case filed before a hand-off beneath the hand-off reference', () => {
    render(<ResultCard lang="en" caseNumber="D-2001" handoffTicket="DEMO-1234" />)
    const card = screen.getByRole('region', { name: 'A person will review your request' })
    expect(card).toHaveTextContent('DEMO-1234')
    expect(screen.getByText('Dispute filed earlier, case reference')).toBeInTheDocument()
    expect(screen.getByText('D-2001')).toBeInTheDocument()
  })

  it('names the earlier case in each language', () => {
    for (const [lang, label] of [
      ['es', 'Disputa registrada antes, número de caso'],
      ['pt', 'Contestação registrada antes, número do caso'],
    ] as const) {
      const { unmount } = render(<ResultCard lang={lang} caseNumber="D-2001" handoffTicket="H-1" />)
      expect(screen.getByText(label)).toBeInTheDocument()
      unmount()
    }
  })

  it('labels the number and tells the customer to keep it', () => {
    render(<ResultCard lang="en" caseNumber="D-2001" handoffTicket={null} />)
    expect(screen.getByText('Case reference')).toBeInTheDocument()
    expect(
      screen.getByText('Keep this number for any question about your request.'),
    ).toBeInTheDocument()
  })

  it('has no accessibility violations in any outcome', async () => {
    for (const [caseNumber, handoffTicket] of [
      ['D-2001', null],
      [null, 'DEMO-1234'],
      ['D-2001', 'DEMO-1234'],
      [null, null],
    ] as const) {
      const { container, unmount } = render(
        <ResultCard lang="en" caseNumber={caseNumber} handoffTicket={handoffTicket} />,
      )
      expect(await axe(container)).toHaveNoViolations()
      unmount()
    }
  })
})
