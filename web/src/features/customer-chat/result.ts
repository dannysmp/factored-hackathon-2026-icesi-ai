/** How a finished conversation turned out. */
export type ResultVariant = 'filed' | 'escalated' | 'closed'

/** The outcome of a finished conversation and the numbers the screen shows for it. */
export interface Result {
  variant: ResultVariant
  /** The number to quote for the outcome, or `null` when there is none. */
  reference: string | null
  /** A dispute filed earlier in a conversation that then ended in a hand-off; `null` otherwise. */
  filedEarlier: string | null
}

/**
 * The outcome and the reference to quote for it. A conversation that ended in a hand-off leads
 * with the hand-off reference, since that is how it ended, and still carries the case filed
 * before it so that number is not lost from the screen. Without a hand-off, a filed case is the
 * outcome; with neither, the conversation simply ended.
 */
export function resultOf(caseNumber: string | null, handoffTicket: string | null): Result {
  if (handoffTicket !== null) {
    return { variant: 'escalated', reference: handoffTicket, filedEarlier: caseNumber }
  }
  if (caseNumber !== null) return { variant: 'filed', reference: caseNumber, filedEarlier: null }
  return { variant: 'closed', reference: null, filedEarlier: null }
}
