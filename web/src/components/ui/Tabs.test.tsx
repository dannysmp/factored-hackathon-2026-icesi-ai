/** Component test: the vendored Radix tabs primitive — click and keyboard switching, and that
 * the wrapper's own styling hooks don't break Radix's tested ARIA pattern. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { Tabs, TabsContent, TabsList, TabsTrigger } from './Tabs'
import tabsCss from './Tabs.css?raw'

function renderTabs(): ReturnType<typeof render> {
  return render(
    <Tabs defaultValue="all">
      <TabsList aria-label="Filter">
        <TabsTrigger value="all">All</TabsTrigger>
        <TabsTrigger value="fraud">Fraud</TabsTrigger>
        <TabsTrigger value="portuguese" disabled>
          Portuguese
        </TabsTrigger>
      </TabsList>
      <TabsContent value="all">Every ticket</TabsContent>
      <TabsContent value="fraud">Fraud tickets only</TabsContent>
      <TabsContent value="portuguese">Portuguese tickets only</TabsContent>
    </Tabs>,
  )
}

describe('Tabs', () => {
  it('shows the default tab and switches on click', async () => {
    const user = userEvent.setup()
    renderTabs()

    expect(screen.getByText('Every ticket')).toBeVisible()
    expect(screen.queryByText('Fraud tickets only')).not.toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Fraud' }))

    expect(screen.getByText('Fraud tickets only')).toBeVisible()
    expect(screen.queryByText('Every ticket')).not.toBeInTheDocument()
  })

  it("switches tabs with the arrow keys, Radix's own roving-tabindex pattern", async () => {
    const user = userEvent.setup()
    renderTabs()

    screen.getByRole('tab', { name: 'All' }).focus()
    await user.keyboard('{ArrowRight}')

    expect(screen.getByRole('tab', { name: 'Fraud' })).toHaveFocus()
    expect(screen.getByText('Fraud tickets only')).toBeVisible()
  })

  it('marks a disabled tab with a strike-through so it differs from an inactive tab without colour', () => {
    const rule = /\.tabs-trigger:disabled\s*\{([^}]*)\}/.exec(tabsCss)

    expect(rule?.[1]).toMatch(/text-decoration:\s*line-through/)
  })

  it('never lands keyboard focus on a disabled tab', async () => {
    const user = userEvent.setup()
    renderTabs()

    screen.getByRole('tab', { name: 'Fraud' }).focus()
    await user.keyboard('{ArrowRight}')

    // Portuguese is disabled: Radix's own roving-tabindex skips it and wraps back to All.
    expect(screen.getByRole('tab', { name: 'All' })).toHaveFocus()
  })

  it('marks the active tab with aria-selected, not just a visual class', () => {
    renderTabs()

    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Fraud' })).toHaveAttribute('aria-selected', 'false')
  })

  it('has no detectable accessibility violation', async () => {
    const { container } = renderTabs()

    expect(await axe(container)).toHaveNoViolations()
  })

  it('keeps the base styling hook alongside a caller-supplied className', () => {
    render(
      <Tabs defaultValue="all">
        <TabsList className="queue-tabs">
          <TabsTrigger value="all">All</TabsTrigger>
        </TabsList>
        <TabsContent value="all">Every ticket</TabsContent>
      </Tabs>,
    )

    expect(screen.getByRole('tablist')).toHaveClass('tabs-list', 'queue-tabs')
  })
})
