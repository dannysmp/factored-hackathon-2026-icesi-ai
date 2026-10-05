/** How a finished conversation turned out. */
export type ResultVariant = 'filed' | 'escalated' | 'closed'

/** The outcome and the reference to quote for it, read from the last turn's identifiers. A filed case wins over a hand-off when both are present. */
export function resultOf(
  caseNumber: string | null,
  handoffTicket: string | null,
): { variant: ResultVariant; reference: string | null } {
  if (caseNumber !== null) return { variant: 'filed', reference: caseNumber }
  if (handoffTicket !== null) return { variant: 'escalated', reference: handoffTicket }
  return { variant: 'closed', reference: null }
}
