import { useId } from 'react'
import type { JSX } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../../components/ui/Tabs'
import type { Lang } from '../../customer-chat/contracts'
import { LANGUAGE_LABELS } from '../labels'

export type TriggerView = 'all' | 'priority' | 'other'

const TRIGGER_VIEWS: readonly { value: TriggerView; label: string }[] = [
  { value: 'all', label: 'Todos' },
  { value: 'priority', label: 'Fraude y pérdida de tarjeta' },
  { value: 'other', label: 'Otros' },
]

/**
 * The queue's two filters (AC-E10-01): language, a plain accessible control (matching
 * `SignInScreen`'s own `<select>` convention, since language composes with the trigger view
 * rather than partitioning it into exclusive panels — AC-E10-04 asks for a ticket that is *both*
 * Portuguese *and* fraud, not one or the other); and the trigger view, three tabs built on the
 * `Tabs` primitive (ADR-19, "the console's tabs ... over the console's queue views"), each a
 * genuine content panel — a shorter, differently prioritized reading of the same queue, not a
 * toggle dressed as tabs. "Fraude y pérdida de tarjeta" surfaces exactly the two triggers
 * AC-E10-01 sorts first, matching the queue's own priority flag (`QueueItem.priority`) rather
 * than one specific `HandoffTrigger` value, so an agent's fraud or card-loss specialty has a
 * direct answer without knowing the full trigger vocabulary.
 *
 * Copy is fixed Spanish, not a catalog entry (D91: the console stays fixed-Spanish and never
 * imports the trilingual `useT` hook chat and sign-in use).
 *
 * Each tab shows how many cases it holds under the current language filter, so the agent knows
 * what is behind a tab before opening it.
 *
 * `renderTable` is called once per trigger view's `TabsContent`, filtered to that view by the
 * caller — this component owns the filter controls, never the table itself.
 */
export function QueueFilters({
  language,
  onLanguageChange,
  counts,
  triggerView,
  onTriggerViewChange,
  renderTable,
}: {
  language: Lang | undefined
  onLanguageChange: (language: Lang | undefined) => void
  counts: Readonly<Record<TriggerView, number>>
  triggerView: TriggerView
  onTriggerViewChange: (view: TriggerView) => void
  renderTable: (view: TriggerView) => JSX.Element
}): JSX.Element {
  const languageFieldId = useId()

  return (
    <Tabs
      value={triggerView}
      onValueChange={(value) => {
        onTriggerViewChange(value as TriggerView)
      }}
    >
      <div className="queue-filters">
        <div className="queue-language-field">
          <label htmlFor={languageFieldId}>Idioma</label>
          <select
            id={languageFieldId}
            value={language ?? 'all'}
            onChange={(event) => {
              const { value } = event.target
              onLanguageChange(value === 'all' ? undefined : (value as Lang))
            }}
          >
            <option value="all">Todos los idiomas</option>
            {(Object.entries(LANGUAGE_LABELS) as [Lang, string][]).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>

        <TabsList aria-label="Filtrar por motivo">
          {TRIGGER_VIEWS.map((view) => (
            <TabsTrigger key={view.value} value={view.value}>
              {view.label} ({counts[view.value]})
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      {TRIGGER_VIEWS.map((view) => (
        <TabsContent key={view.value} value={view.value}>
          {renderTable(view.value)}
        </TabsContent>
      ))}
    </Tabs>
  )
}
