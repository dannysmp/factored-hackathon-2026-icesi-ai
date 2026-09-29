/**
 * Queue client: the one seam between the console UI and a queue source.
 *
 * `FixtureQueueClient` replays a fixed queue and applies the same `language`/`trigger` filter
 * the real `GET /v1/agent/queue` route applies server side (`contracts/service_v1/console.py`'s
 * own filtering), so the screen behaves identically against either source. The live HTTP client
 * is a follow-up slice's: `app/api/agent.py`'s router is not wired into `app/main.py` yet (its
 * `ConsoleAuditSink` has no real implementation), so there is no endpoint to call yet.
 */
import type { QueueFilters, QueueItem, QueueResponse } from './contracts'
import { QueueResponseSchema } from './contracts'

export interface QueueClient {
  fetchQueue: (filters: QueueFilters) => Promise<QueueResponse>
}

function matchesFilters(item: QueueItem, filters: QueueFilters): boolean {
  if (filters.language !== undefined && item.language !== filters.language) {
    return false
  }
  if (filters.trigger !== undefined && item.trigger !== filters.trigger) {
    return false
  }
  return true
}

/**
 * Replays a fixed queue snapshot, filtered in the same terms as the real route.
 *
 * It does not track ticket state across calls (claim, resolve, ...): the console is a read-only
 * viewer (AC-E10-09), and a fixture has nothing to mutate in the first place.
 */
export class FixtureQueueClient implements QueueClient {
  private readonly response: QueueResponse

  constructor(response: unknown) {
    this.response = QueueResponseSchema.parse(response)
  }

  fetchQueue(filters: QueueFilters): Promise<QueueResponse> {
    return Promise.resolve({
      ...this.response,
      items: this.response.items.filter((item) => matchesFilters(item, filters)),
    })
  }
}
