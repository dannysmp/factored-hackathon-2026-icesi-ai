/**
 * The agent console's queue screen: the handoff queue with its language filter and trigger views,
 * and every load state around it.
 */
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

/**
 * The items of one trigger view. The view is a pure filter over the loaded queue: `priority` keeps
 * the fraud and card-loss rows (`QueueItem.priority`), `other` keeps the rest, `all` keeps every row.
 */
function itemsForView(items: readonly QueueItem[], view: TriggerView): QueueItem[] {
  if (view === 'all') return [...items]
  if (view === 'priority') return items.filter((item) => item.priority)
  return items.filter((item) => !item.priority)
}

/**
 * The console's handoff queue: every screen state rendered deliberately (loading, error with a
 * retry, empty, a background refetch over already-loaded data, and the filtered table), matching
 * `ChatFeature`'s own rule that a blank screen or a raw error is never acceptable.
 *
 * Fixed Spanish copy, not a catalog entry: the console is deliberately fixed-Spanish and never
 * uses the per-language catalogs or the trilingual `useT` hook that chat and sign-in use.
 * A language-filter change refetches while the previous table stays on screen; the trigger view is
 * local state because it only reshapes rows already loaded.
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

  // Distinct, non-overlapping treatments: the initial load, an empty queue, and the
  // filters-plus-table view. In that view the trigger tabs stay while `QueueTable` shows its own
  // "nothing for this filter" message. A filtered result with no matches stays in that view so the
  // filter can be undone; only an unfiltered empty queue takes the empty treatment and has no
  // filters. `showUpdating`, below, adds a further treatment when a filter refetch is in flight
  // over data already on screen.
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
