/** Maps a classified request failure to the sentence that explains it to the person. */
import type { FailureKind } from '../lib/failure'
import type { Messages } from './messages'

/** The catalog key per failure kind; kinds absent here have no reason beyond the failure title. */
const REASON_KEYS: Partial<Record<FailureKind, keyof Messages>> = {
  unauthorized: 'app.sessionExpired',
  rateLimited: 'failure.rateLimited',
  unavailable: 'failure.unavailable',
  timeout: 'failure.timeout',
  offline: 'failure.offline',
}

/**
 * The sentence that tells the person why a request failed and what to do, in their language, or
 * `undefined` when there is nothing to add beyond the message that names what did not work.
 */
export function failureReason(
  kind: FailureKind | null,
  t: (key: keyof Messages) => string,
): string | undefined {
  const key = kind === null ? undefined : REASON_KEYS[kind]
  return key === undefined ? undefined : t(key)
}
