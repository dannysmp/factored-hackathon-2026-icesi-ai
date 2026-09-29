/** Unit tests: `FixtureTicketDetailClient` answers by `ticket_ref`, `null` for an unknown one. */
import { describe, expect, it } from 'vitest'
import { FixtureTicketDetailClient } from './ticketDetailClient'
import { DEMO_TICKET_DETAILS } from './fixtures'

describe('FixtureTicketDetailClient', () => {
  it('answers a known ticket by its own reference', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)
    const [first] = DEMO_TICKET_DETAILS
    expect(first).toBeDefined()

    const detail = await client.fetchTicketDetail(first?.item.ticket_ref ?? '')

    expect(detail?.item.ticket_ref).toBe(first?.item.ticket_ref)
  })

  it('answers null for an unknown reference, never an error', async () => {
    const client = new FixtureTicketDetailClient(DEMO_TICKET_DETAILS)

    const detail = await client.fetchTicketDetail('T-NOT-A-REAL-TICKET')

    expect(detail).toBeNull()
  })

  it('rejects a snapshot that does not match the contract', () => {
    expect(() => new FixtureTicketDetailClient([{ item: { ticket_ref: 'x' } }])).toThrow()
  })
})
