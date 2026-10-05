/** The ticket timeline: a ticket's decisions and reasons as a table, in trace order. */
import { ScrollRegion } from '../../../components/ui/ScrollRegion'
import type { JSX } from 'react'
import type { TimelineEntry } from '../contracts'
import { formatDateTime } from '../format'
import { INTENT_LABELS, REASON_CODE_LABELS, phaseLabel } from '../labels'

/** Orders the audit trail by trace identifier — the backend's own tuple order is not specified
 * to match, so this component orders it rather than assume. */
function byTraceId(a: TimelineEntry, b: TimelineEntry): number {
  return a.trace_id.localeCompare(b.trace_id)
}

/**
 * The timeline: decisions and reasons, in order by trace identifier, never message text —
 * `TimelineEntry` (contracts/service_v1/console.py) has no message-text field at all, so nothing
 * here can expose one. Known intents, phases and reason codes read through their Spanish labels;
 * an empty list renders a plain "no audit records" message instead of an empty table.
 */
export function TimelinePanel({ entries }: { entries: readonly TimelineEntry[] }): JSX.Element {
  if (entries.length === 0) {
    return <p>Ningún registro de auditoría.</p>
  }

  const sorted = [...entries].sort(byTraceId)

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
            <tr key={entry.trace_id}>
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
