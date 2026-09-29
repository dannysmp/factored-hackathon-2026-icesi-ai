/**
 * Queue client: the one seam between the console UI and a queue source.
 *
 * `FixtureQueueClient` replays a fixed queue and applies the same `language`/`trigger` filter
 * the real `GET /v1/agent/queue` route applies server side (`contracts/service_v1/console.py`'s
 * own filtering), so the screen behaves identically against either source. `LiveQueueClient` is
 * the real HTTP client, behind an agent's own demo session (now wired into `app/main.py`).
 */
import type { QueueFilters, QueueItem, QueueResponse } from './contracts'
import { QueueResponseSchema } from './contracts'

const QUEUE_PATH = '/v1/agent/queue'

export interface QueueClient {
  fetchQueue: (filters: QueueFilters) => Promise<QueueResponse>
}

/** Raised when the queue route refuses a request; `message` is the problem document's own title,
 * safe to show an agent (never the raw response body). */
export class AgentRequestError extends Error {
  readonly status: number

  constructor(status: number, title: string) {
    super(title)
    this.name = 'AgentRequestError'
    this.status = status
  }
}

export async function toAgentError(response: Response): Promise<AgentRequestError> {
  try {
    const problem: unknown = await response.json()
    const title =
      typeof problem === 'object' && problem !== null && 'title' in problem
        ? String(problem.title)
        : response.statusText
    return new AgentRequestError(response.status, title)
  } catch {
    return new AgentRequestError(response.status, response.statusText || 'the request failed')
  }
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

/** The real queue client, against `GET /v1/agent/queue` behind an agent's own demo session. */
export class LiveQueueClient implements QueueClient {
  private readonly token: string

  constructor({ token }: { token: string }) {
    this.token = token
  }

  async fetchQueue(filters: QueueFilters): Promise<QueueResponse> {
    const params = new URLSearchParams()
    if (filters.language !== undefined) params.set('language', filters.language)
    if (filters.trigger !== undefined) params.set('trigger', filters.trigger)
    const query = params.toString()
    const response = await fetch(query === '' ? QUEUE_PATH : `${QUEUE_PATH}?${query}`, {
      headers: { Authorization: `Bearer ${this.token}` },
    })
    if (!response.ok) {
      throw await toAgentError(response)
    }
    return QueueResponseSchema.parse(await response.json())
  }
}
