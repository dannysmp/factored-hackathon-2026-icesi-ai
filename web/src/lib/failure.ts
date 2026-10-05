/**
 * What went wrong with a request, in the terms a person can act on.
 *
 * Every call to the service can fail in ways that need different words and different next steps:
 * a refusal of the credentials, a limit reached, a slow or unreachable connection, a service
 * that is down. The callers keep their own error classes; this module reads the one thing they
 * share, the HTTP status, plus the two ways a `fetch` fails before any status exists.
 */

/**
 * How long a request may take before the person is told, so a stalled connection never leaves a
 * spinner running forever. It sits well above the service's own limit for one model call (eight
 * seconds), so a turn that needs a few calls still completes.
 */
export const REQUEST_TIMEOUT_MS = 30_000

/** The categories of failure a person can act on differently; `other` is the unclassified rest. */
export type FailureKind =
  /** The service refused the credentials: a wrong access code, or a session that has ended. */
  | 'unauthorized'
  /** A limit was reached; waiting a moment is the way forward. */
  | 'rateLimited'
  /** The service answered that it cannot serve the request right now. */
  | 'unavailable'
  /** No answer arrived in time. */
  | 'timeout'
  /** The request never reached the service. */
  | 'offline'
  | 'other'

/** A signal that aborts the request once it has taken longer than `REQUEST_TIMEOUT_MS`. */
export function requestSignal(): AbortSignal {
  return AbortSignal.timeout(REQUEST_TIMEOUT_MS)
}

/** The numeric HTTP `status` an error carries, or `null` when it carries none. */
function statusOf(error: unknown): number | null {
  if (typeof error === 'object' && error !== null && 'status' in error) {
    const status = error.status
    return typeof status === 'number' ? status : null
  }
  return null
}

/**
 * Sorts a failed request into the kind of problem it was. The HTTP status wins over the shape of
 * the error; an abort that is not the request timeout is `other`, so a deliberate cancel is never
 * reported as a slow connection.
 */
export function classifyFailure(error: unknown): FailureKind {
  const status = statusOf(error)
  if (status === 401) return 'unauthorized'
  if (status === 429) return 'rateLimited'
  if (status !== null && status >= 500) return 'unavailable'
  if (error instanceof DOMException && error.name === 'TimeoutError') return 'timeout'
  // `fetch` rejects with a `TypeError` when the request could not be made at all.
  if (error instanceof TypeError) return 'offline'
  return 'other'
}
