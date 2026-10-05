/** The queue table: one row per escalated case, with an accessible button to open it. */
import { useEffect, useRef } from 'react'
import { ScrollRegion } from '../../../components/ui/ScrollRegion'
import type { JSX } from 'react'
import type { QueueItem } from '../contracts'
import { formatAge, formatDate } from '../format'
import { CATEGORY_LABELS, LANGUAGE_LABELS, STATUS_LABELS, TRIGGER_LABELS } from '../labels'

/** Whether a case has run past the queue's contact-by target date — the reference
 * date and `promised_contact_by` are both plain ISO dates (`YYYY-MM-DD`), so a lexicographic
 * comparison is a correct date comparison. */
function isOverdue(referenceDate: string, promisedContactBy: string): boolean {
  return referenceDate > promisedContactBy
}

/** The mark a priority case carries: an icon and the word, so it never rests on colour or weight alone. */
function PriorityBadge(): JSX.Element {
  return (
    <span className="queue-priority-badge">
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path d="M3 1.5v13M3 2h9l-2 3.25L12 8.5H3" />
      </svg>
      Prioritario
    </span>
  )
}

/**
 * One trigger view's rows: reference, trigger, language, category, age against the promised
 * contact time, status. `items` is already in the order its source guarantees (priority cases
 * first, `contracts/service_v1/console.py`'s `QueueResponse`) — this component does not re-sort,
 * the same "a fixture does not recompute what its source already decided" rule `FixtureChatClient`
 * follows. An empty `items` renders a "no case matches this filter" message instead of a table.
 *
 * A case's own reference is a real button, not a styled link over a `<th>` (semantic HTML first)
 * — clicking it calls `onSelectTicket`, `ConsoleApp`'s master-detail navigation into that case's
 * packet and timeline. Priority rows carry a text badge, and an overdue promised contact date is
 * flagged in words, so neither meaning rests on colour alone.
 *
 * `focusTicketRef` names the case the agent has just come back from: when it is in this view and
 * nothing holds focus, its button takes focus, so going back lands on the row that was open.
 * The request is single-use: `onFocusHandled` is called once the table has acted on it, whether or
 * not the row was found or focus was taken, so a later remount of the table never replays it.
 */
export function QueueTable({
  items,
  onSelectTicket,
  focusTicketRef = null,
  onFocusHandled,
}: {
  items: readonly QueueItem[]
  onSelectTicket: (ticketRef: string) => void
  focusTicketRef?: string | null
  onFocusHandled?: () => void
}): JSX.Element {
  const returnedButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (focusTicketRef === null) return
    if (document.activeElement === document.body) returnedButtonRef.current?.focus()
    onFocusHandled?.()
  }, [focusTicketRef, onFocusHandled])

  if (items.length === 0) {
    return <p>Ningún caso coincide con este filtro.</p>
  }

  return (
    // From the tablet breakpoint up, a narrower viewport scrolls this wrapper horizontally rather
    // than wrapping every cell's text across several lines; below it, each row is a card. Each value
    // cell holds a single box so the card's label/value grid places the whole value beside its label.
    <ScrollRegion className="queue-table-scroll queue-table-openable" label="Casos escalados">
      <table className="stacked-table">
        <caption className="sr-only">Casos escalados</caption>
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
                    ref={item.ticket_ref === focusTicketRef ? returnedButtonRef : undefined}
                    type="button"
                    className="queue-ticket-ref-button"
                    onClick={() => {
                      onSelectTicket(item.ticket_ref)
                    }}
                  >
                    {item.ticket_ref}
                  </button>
                  {item.priority && (
                    <>
                      <span className="sr-only">, </span>
                      <PriorityBadge />
                    </>
                  )}
                </th>
                <td data-label="Motivo">{TRIGGER_LABELS[item.trigger]}</td>
                <td data-label="Idioma">{LANGUAGE_LABELS[item.language]}</td>
                <td data-label="Categoría">
                  {item.category === null ? '—' : CATEGORY_LABELS[item.category]}
                </td>
                <td data-label="Antigüedad">{formatAge(item.age_days)}</td>
                <td data-label="Contacto prometido para">
                  <span>
                    {formatDate(item.promised_contact_by)}
                    {overdue && <span className="queue-overdue-flag"> · vencido</span>}
                  </span>
                </td>
                <td data-label="Estado">{STATUS_LABELS[item.status]}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </ScrollRegion>
  )
}
