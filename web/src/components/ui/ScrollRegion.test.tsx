/** Component test: a scroll wrapper is a named, focusable region only while its content overflows. */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScrollRegion } from './ScrollRegion'

function stubOverflow(scrollWidth: number, clientWidth: number): void {
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(scrollWidth)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(clientWidth)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ScrollRegion', () => {
  it('adds neither a landmark nor a tab stop while its content fits', () => {
    stubOverflow(300, 300)
    render(<ScrollRegion label="Tickets">content</ScrollRegion>)

    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(screen.getByText('content')).not.toHaveAttribute('tabindex')
  })

  it('becomes a named, keyboard-focusable region once its content overflows', () => {
    stubOverflow(600, 300)
    render(<ScrollRegion label="Tickets">content</ScrollRegion>)

    const region = screen.getByRole('region', { name: 'Tickets' })
    expect(region).toHaveAttribute('tabindex', '0')
    region.focus()
    expect(region).toHaveFocus()
  })
})
