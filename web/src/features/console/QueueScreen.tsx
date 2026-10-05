import { useState } from 'react'
import type { JSX } from 'react'
import './QueueScreen.css'
import { Button } from '../../components/ui/Button'
import { ReferenceBanner } from '../customer-chat/components/ReferenceBanner'
import { QueueFilters } from './components/QueueFilters'
import type { TriggerView } from './components/QueueFilters'
import { QueueTable } from './components/QueueTable'
import type { QueueClient } from './client'
import type { QueueItem } from './contracts'
import { formatDate } from './format'
import { useQueue } from './useQueue'

function itemsForView(items: readonly QueueItem[], view: TriggerView): QueueItem[] {
  if (view === 'all') return [...items]
  if (view === 'priority') return items.filter((item) => item.priority)
  return items.filter((item) => !item.priority)
}

/**
 * The console's handoff queue (AC-E10-01): every screen state rendered deliberately (loading,
 * error with a retry, empty, a background refetch over already-loaded data, and the filtered
 * table), matching `ChatFeature`'s own rule that a blank screen or a raw error is never acceptable
 * (AC-E10-18).
 *
 * Fixed Spanish copy, not a catalog entry (D91): the console stays fixed-Spanish and never
 * imports the trilingual `useT` hook chat and sign-in use.
 */
export function QueueScreen({
  client,
  onSelectTicket,
  onSessionExpired,
}: {
  client: QueueClient
  onSelectTicket: (ticketRef: string) => void
  onSessionExpired?: () => void
}): JSX.Element {
  const queue = useQueue(client, onSessionExpired)
  const [triggerView, setTriggerView] = useState<TriggerView>('all')

  if (queue.status === 'error') {
    return (
      <div className="queue-screen" role="alert">
        <p>No se pudo cargar la cola. Intente de nuevo.</p>
        <Button variant="primary" onClick={queue.retry}>
          Intentar de nuevo
        </Button>
      </div>
    )
  }

  // Distinct, non-overlapping treatments (AC-E10-18): the initial load, a truly empty queue, and
  // the filters-plus-table view, which may itself show a lesser "nothing for this filter" message
  // (`QueueTable`'s own) without losing the filters that got it there — a different state from
  // having no tickets at all. `showUpdating`, below, adds a further treatment once a filter
  // refetch is in flight over data already on screen.
  const showLoading = queue.status === 'loading' && queue.referenceDate === null
  // Only a queue with no language filter applied is truly empty: with a filter on, zero matches
  // is a narrower result the agent must be able to undo, so the filters stay on screen.
  const showEmpty =
    queue.status === 'ready' && queue.items.length === 0 && queue.language === undefined
  // A language-filter change re-issues the fetch without clearing the already-loaded table
  // (`useQueue`'s own `setLanguage` keeps `items`/`referenceDate`, only flips `status`), so the
  // filters and the (still-stale) table stay visible during a refetch — this affordance is the
  // only signal that a request is actually in flight, distinct from `showLoading`'s own first-load
  // treatment, which replaces the table entirely rather than sitting alongside it.
  const showUpdating = queue.status === 'loading' && queue.referenceDate !== null

  return (
    <section className="queue-screen" aria-label="Cola de casos escalados">
      <ReferenceBanner
        referenceDateLine={
          queue.referenceDate === null
            ? 'Cargando la fecha de referencia de los datos…'
            : `Fecha de referencia de los datos: ${formatDate(queue.referenceDate)}.`
        }
        demoNotice="Esta es una sesión de demostración."
      />
      {showLoading && (
        <p aria-live="polite" role="status">
          Cargando la cola…
        </p>
      )}
      {showUpdating && (
        <p className="queue-updating" aria-live="polite" role="status">
          Actualizando…
        </p>
      )}
      {showEmpty && <p>No hay casos abiertos en este momento.</p>}
      {!showLoading && !showEmpty && (
        <QueueFilters
          language={queue.language}
          onLanguageChange={queue.setLanguage}
          counts={{
            all: queue.items.length,
            priority: itemsForView(queue.items, 'priority').length,
            other: itemsForView(queue.items, 'other').length,
          }}
          triggerView={triggerView}
          onTriggerViewChange={setTriggerView}
          renderTable={(view) => (
            <QueueTable items={itemsForView(queue.items, view)} onSelectTicket={onSelectTicket} />
          )}
        />
      )}
    </section>
  )
}
