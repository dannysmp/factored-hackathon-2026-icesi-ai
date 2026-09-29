import type { JSX } from 'react'
import type { TimelineEntry } from '../contracts'
import { INTENT_LABELS, REASON_CODE_LABELS } from '../labels'

/** The audit trail, in order by trace identifier (AC-E10-03) — the backend's own tuple order is
 * not itself specified to match, so this component orders it rather than assume. */
function byTraceId(a: TimelineEntry, b: TimelineEntry): number {
  return a.trace_id.localeCompare(b.trace_id)
}

/**
 * The timeline (AC-E10-03): decisions and reasons, in order by trace identifier, never message
 * text — `TimelineEntry` (contracts/service_v1/console.py) has no message-text field at all, so
 * nothing here can expose one (AC-E10-05).
 */
export function TimelinePanel({ entries }: { entries: readonly TimelineEntry[] }): JSX.Element {
  if (entries.length === 0) {
    return <p>Ningún registro de auditoría.</p>
  }

  const sorted = [...entries].sort(byTraceId)

  return (
    <div className="queue-table-scroll">
      <table>
        <caption className="sr-only">Cronología de auditoría</caption>
        <thead>
          <tr>
            <th scope="col">Identificador de traza</th>
            <th scope="col">Momento</th>
            <th scope="col">Tipo</th>
            <th scope="col">Estado</th>
            <th scope="col">Código de razón</th>
            <th scope="col">Versión de política</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <tr key={entry.trace_id}>
              <th scope="row">{entry.trace_id}</th>
              <td>{entry.occurred_at}</td>
              <td>{INTENT_LABELS[entry.intent]}</td>
              <td>
                {entry.state_before} → {entry.state_after}
              </td>
              <td>{entry.reason_code === null ? '—' : REASON_CODE_LABELS[entry.reason_code]}</td>
              <td>{entry.policy_version ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
