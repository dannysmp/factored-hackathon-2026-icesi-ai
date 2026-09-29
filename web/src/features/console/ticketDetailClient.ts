/**
 * Ticket-detail client: the one seam between the UI and a ticket's packet and timeline.
 *
 * `FixtureTicketDetailClient` answers from a fixed map, keyed by `ticket_ref`, exactly like the
 * real `GET /v1/agent/tickets/{ticket_ref}` route (`app/api/agent.py`) would: an unknown reference
 * answers `null`, never an error — the console has no foreign-reference disguise to preserve for
 * an agent session, unlike the customer-scoped tools. The live HTTP client is a follow-up slice's:
 * the route shares one `APIRouter` with the queue route (`build_agent_router`), neither of which
 * is wired into `app/main.py` yet (the ticket route's `ConsoleAuditSink` has no real
 * implementation), so there is no endpoint to call yet.
 */
import type { TicketDetail } from './contracts'
import { TicketDetailSchema } from './contracts'

export interface TicketDetailClient {
  fetchTicketDetail: (ticketRef: string) => Promise<TicketDetail | null>
}

/**
 * Replays a fixed set of ticket details, keyed by their own `ticket_ref`.
 *
 * It does not track state across calls (claim, resolve, ...): the console is a read-only viewer
 * (AC-E10-09), and a fixture has nothing to mutate in the first place.
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
