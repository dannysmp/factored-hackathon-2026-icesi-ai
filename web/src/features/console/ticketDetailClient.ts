/**
 * Ticket-detail client: the one seam between the UI and a ticket's packet and timeline.
 *
 * `FixtureTicketDetailClient` answers from a fixed map, keyed by `ticket_ref`, exactly like the
 * real `GET /v1/agent/tickets/{ticket_ref}` route (`app/api/agent.py`) would: an unknown reference
 * answers `null`, never an error — the console has no foreign-reference disguise to preserve for
 * an agent session, unlike the customer-scoped tools. `LiveTicketDetailClient` is the real HTTP
 * client, behind an agent's own demo session; it maps the route's own 404 to `null` the same way,
 * and any other failure to `AgentRequestError`.
 */
import { toAgentError } from './client'
import type { TicketDetail } from './contracts'
import { TicketDetailSchema } from './contracts'

const TICKETS_PATH = '/v1/agent/tickets'

/** A source of one ticket's packet and timeline; resolves `null` when the reference is unknown. */
export interface TicketDetailClient {
  fetchTicketDetail: (ticketRef: string) => Promise<TicketDetail | null>
}

/**
 * Replays a fixed set of ticket details, keyed by their own `ticket_ref`.
 *
 * It does not track state across calls (claim, resolve, ...): the console is a read-only viewer,
 * and a fixture has nothing to mutate in the first place.
 */
export class FixtureTicketDetailClient implements TicketDetailClient {
  private readonly details: ReadonlyMap<string, TicketDetail>

  constructor(details: readonly unknown[]) {
    const parsed = details.map((detail) => TicketDetailSchema.parse(detail))
    this.details = new Map(parsed.map((detail) => [detail.item.ticket_ref, detail]))
  }

  fetchTicketDetail(ticketRef: string): Promise<TicketDetail | null> {
    return Promise.resolve(this.details.get(ticketRef) ?? null)
  }
}

/** The real ticket-detail client, against `GET /v1/agent/tickets/{ticket_ref}` behind an agent's
 * own demo session. The reference is URL-encoded and the payload validated against
 * `TicketDetailSchema`. */
export class LiveTicketDetailClient implements TicketDetailClient {
  private readonly token: string

  constructor({ token }: { token: string }) {
    this.token = token
  }

  async fetchTicketDetail(ticketRef: string): Promise<TicketDetail | null> {
    const response = await fetch(`${TICKETS_PATH}/${encodeURIComponent(ticketRef)}`, {
      headers: { Authorization: `Bearer ${this.token}` },
    })
    if (response.status === 404) {
      return null
    }
    if (!response.ok) {
      throw await toAgentError(response)
    }
    return TicketDetailSchema.parse(await response.json())
  }
}
