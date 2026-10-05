/** The ticket timeline: a ticket's decisions and reasons as a table, in the order they happened. */
import { ScrollRegion } from '../../../components/ui/ScrollRegion'
import type { JSX } from 'react'
import type { TimelineEntry } from '../contracts'
import { formatDateTime } from '../format'
import { INTENT_LABELS, REASON_CODE_LABELS, phaseLabel } from '../labels'

/** Earliest first. The service sends every moment in UTC, so the instants compare directly; entries
 * at the same instant (including ones less than a millisecond apart, which `Date.parse` cannot
 * tell apart) keep the order they arrived in, since `sort` is stable. */
function byMoment(a: TimelineEntry, b: TimelineEntry): number {
  return Date.parse(a.occurred_at) - Date.parse(b.occurred_at)
}

/**
 * The timeline: decisions and reasons, in the order they happened, never message text.
 * `TimelineEntry` (contracts/service_v1/console.py) has no message-text field at all, so nothing
 * here can expose one.
 *
 * The backend's own order is not specified, so this orders by the moment each entry occurred. A
 * trace identifier is shared by every entry of one request and is not a position, so it is shown
 * but neither orders the rows nor identifies one; each row is keyed by its own turn identifier.
 */
export function TimelinePanel({ entries }: { entries: readonly TimelineEntry[] }): JSX.Element {
  if (entries.length === 0) {
    return <p>Ningún registro de auditoría.</p>
  }

  const sorted = [...entries].sort(byMoment)

  return (
    <ScrollRegion className="queue-table-scroll" label="Cronología de auditoría">
      <table className="timeline-table">
        <caption className="sr-only">Cronología de auditoría</caption>
        <thead>
          <tr>
            <th scope="col">Momento</th>
            <th scope="col">Paso</th>
            <th scope="col">Etapa</th>
            <th scope="col">Motivo de la decisión</th>
            <th scope="col">Versión de la política</th>
            <th scope="col">Traza</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <tr key={entry.turn_id}>
              <th scope="row">{formatDateTime(entry.occurred_at)}</th>
              <td>{INTENT_LABELS[entry.intent]}</td>
              <td>
                {phaseLabel(entry.state_before)} → {phaseLabel(entry.state_after)}
              </td>
              <td>{entry.reason_code === null ? '—' : REASON_CODE_LABELS[entry.reason_code]}</td>
              <td>{entry.policy_version ?? '—'}</td>
              <td className="timeline-trace">{entry.trace_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRegion>
  )
}
