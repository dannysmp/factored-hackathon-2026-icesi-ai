/**
 * Component test: `QueueFilters` reports the chosen language, and shows one tab per trigger view
 * with its case count, rendering only the active view's table and reporting a newly selected view.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { QueueFilters } from './QueueFilters'
import type { TriggerView } from './QueueFilters'

function renderFilters(triggerView: TriggerView, onTriggerViewChange = vi.fn()) {
  const onLanguageChange = vi.fn()
  render(
    <QueueFilters
      language={undefined}
      onLanguageChange={onLanguageChange}
      counts={{ all: 5, priority: 2, other: 3 }}
      triggerView={triggerView}
      onTriggerViewChange={onTriggerViewChange}
      renderTable={(view) => <p>Table for {view}</p>}
    />,
  )
  return { onLanguageChange, onTriggerViewChange }
}

describe('QueueFilters', () => {
  it('reports the chosen language, and undefined for "all languages"', async () => {
    const user = userEvent.setup()
    const { onLanguageChange } = renderFilters('all')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'pt')
    expect(onLanguageChange).toHaveBeenLastCalledWith('pt')

    await user.selectOptions(screen.getByLabelText('Idioma'), 'all')
    expect(onLanguageChange).toHaveBeenLastCalledWith(undefined)
  })

  it('renders only the active trigger view’s table', () => {
    renderFilters('priority')

    expect(screen.getByText('Table for priority')).toBeInTheDocument()
    expect(screen.queryByText('Table for all')).not.toBeInTheDocument()
    expect(screen.queryByText('Table for other')).not.toBeInTheDocument()
  })

  it('shows how many cases each view holds in its tab', () => {
    renderFilters('all')

    expect(screen.getByRole('tab', { name: 'Todos (5)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Fraude y pérdida de tarjeta (2)' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Otros (3)' })).toBeInTheDocument()
  })

  it('reports the newly selected trigger view on click', async () => {
    const user = userEvent.setup()
    const { onTriggerViewChange } = renderFilters('all')

    await user.click(screen.getByRole('tab', { name: 'Fraude y pérdida de tarjeta (2)' }))

    expect(onTriggerViewChange).toHaveBeenCalledWith('priority')
  })
})
