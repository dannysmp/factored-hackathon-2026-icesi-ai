import { useState } from 'react'
import type { JSX } from 'react'
import './QueueScreen.css'
import { ReferenceBanner } from '../customer-chat/components/ReferenceBanner'
import { QueueFilters } from './components/QueueFilters'
import type { TriggerView } from './components/QueueFilters'
import { QueueTable } from './components/QueueTable'
import type { QueueClient } from './client'
import type { QueueItem } from './contracts'
import { useQueue } from './useQueue'

function itemsForView(items: readonly QueueItem[], view: TriggerView): QueueItem[] {
  if (view === 'all') return [...items]
  if (view === 'priority') return items.filter((item) => item.priority)
  return items.filter((item) => !item.priority)
}

/**
 * The console's handoff queue (AC-E10-01): every screen state rendered deliberately (the initial
 * load, error with a retry, empty, the filtered table, and a filter-triggered refetch over the
 * table already on screen), matching `ChatFeature`'s own rule that a blank screen or a raw error
 * is never acceptable (AC-E10-18).
 *
 * Fixed Spanish copy, not a catalog entry (D91): the console stays fixed-Spanish and never
 * imports the trilingual `useT` hook chat and sign-in use.
 */
export function QueueScreen({
  client,
  onSelectTicket,
}: {
  client: QueueClient
  onSelectTicket: (ticketRef: string) => void
}): JSX.Element {
  const queue = useQueue(client)
  const [triggerView, setTriggerView] = useState<TriggerView>('all')

  if (queue.status === 'error') {
    return (
      <div className="queue-screen" role="alert">
        <p>No se pudo cargar la cola. Intente de nuevo.</p>
        <button type="button" onClick={queue.retry}>
          Intentar de nuevo
        </button>
      </div>
    )
  }

  // Non-overlapping treatments (AC-E10-18): the initial load, a truly empty queue, and the
  // filters-plus-table view, which may itself show a lesser "nothing for this filter" message
  // (`QueueTable`'s own) without losing the filters that got it there — a different state from
  // having no tickets at all.
  const showLoading = queue.status === 'loading' && queue.referenceDate === null
  const showEmpty = queue.status === 'ready' && queue.items.length === 0
  // A language-filter change re-issues the fetch without clearing referenceDate/items (useQueue
  // keeps the previous response visible while the new one is in flight), so this is a distinct
  // fifth state from the initial load above: the table stays on screen, showing the previous
  // response, with an inline affordance saying a refetch is under way rather than no feedback at
  // all while a new response arrives.
  const isRefetching = queue.status === 'loading' && queue.referenceDate !== null

  return (
    <section className="queue-screen" aria-label="Cola de casos escalados">
      <ReferenceBanner
        referenceDateLine={
          queue.referenceDate === null
            ? 'Cargando la fecha de referencia de los datos…'
            : `Fecha de referencia de los datos: ${queue.referenceDate}.`
        }
        demoNotice="Esta es una sesión de demostración."
      />
      {showLoading && (
        <p aria-live="polite" role="status">
          Cargando la cola…
        </p>
      )}
      {showEmpty && <p>No hay tickets abiertos en este momento.</p>}
      {!showLoading && !showEmpty && (
        <>
          {isRefetching && (
            <p aria-live="polite" role="status">
              Actualizando…
            </p>
          )}
          <QueueFilters
            language={queue.language}
            onLanguageChange={queue.setLanguage}
            triggerView={triggerView}
            onTriggerViewChange={setTriggerView}
            renderTable={(view) => (
              <QueueTable items={itemsForView(queue.items, view)} onSelectTicket={onSelectTicket} />
            )}
          />
        </>
      )}
    </section>
  )
}
