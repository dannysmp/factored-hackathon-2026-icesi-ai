import type { JSX } from 'react'
import type { QueueItem } from '../contracts'
import { CATEGORY_LABELS, LANGUAGE_LABELS, STATUS_LABELS, TRIGGER_LABELS } from '../labels'

/** Whether a ticket has run past the contact time the customer was promised — the reference
 * date and `promised_contact_by` are both plain ISO dates (`YYYY-MM-DD`), so a lexicographic
 * comparison is a correct date comparison. */
function isOverdue(referenceDate: string, promisedContactBy: string): boolean {
  return referenceDate > promisedContactBy
}

/**
 * One trigger view's rows (AC-E10-01: reference, trigger, language, category, age against the
 * promised contact time, status). `items` is already in the order its source guarantees
 * (priority tickets first, `contracts/service_v1/console.py`'s own `QueueResponse`) — this
 * component does not re-sort, the same "a fixture does not recompute what its source already
 * decided" rule `FixtureChatClient` follows.
 *
 * A ticket's own reference is a real button, not a styled link over a `<th>` (frontend standard,
 * section 7: semantic HTML first) — clicking it calls `onSelectTicket`, `ConsoleApp`'s own
 * master-detail navigation into that ticket's packet and timeline.
 */
export function QueueTable({
  items,
  onSelectTicket,
}: {
  items: readonly QueueItem[]
  onSelectTicket: (ticketRef: string) => void
}): JSX.Element {
  if (items.length === 0) {
    return <p>Ningún ticket coincide con este filtro.</p>
  }

  return (
    // A narrow viewport scrolls this wrapper horizontally rather than wrapping every cell's text
    // across several lines (AC-E10-20: no truncated or illegible text at the mobile breakpoint) —
    // the same accepted pattern `Tabs.css`'s own trigger list already uses for the same reason.
    <div className="queue-table-scroll queue-table-openable">
      <table>
        <caption className="sr-only">Tickets escalados</caption>
        <thead>
          <tr>
            <th scope="col">Referencia</th>
            <th scope="col">Motivo</th>
            <th scope="col">Idioma</th>
            <th scope="col">Categoría</th>
            <th scope="col">Antigüedad</th>
            <th scope="col">Contacto prometido para</th>
            <th scope="col">Estado</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const overdue = isOverdue(item.reference_date, item.promised_contact_by)
            return (
              <tr
                key={item.ticket_ref}
                className={item.priority ? 'queue-row-priority' : undefined}
              >
                <th scope="row">
                  <button
                    type="button"
                    className="queue-ticket-ref-button"
                    onClick={() => {
                      onSelectTicket(item.ticket_ref)
                    }}
                  >
                    {item.ticket_ref}
                  </button>
                </th>
                <td>{TRIGGER_LABELS[item.trigger]}</td>
                <td>{LANGUAGE_LABELS[item.language]}</td>
                <td>{item.category === null ? '—' : CATEGORY_LABELS[item.category]}</td>
                <td>
                  {item.age_days} {item.age_days === 1 ? 'día' : 'días'}
                </td>
                <td>
                  {item.promised_contact_by}
                  {overdue && <span className="queue-overdue-flag"> · vencido</span>}
                </td>
                <td>{STATUS_LABELS[item.status]}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
