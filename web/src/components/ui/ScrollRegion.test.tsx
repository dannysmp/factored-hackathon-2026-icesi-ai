/** Component test: a scroll wrapper is a named, focusable region only while its content overflows. */
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ScrollRegion } from './ScrollRegion'

function stubOverflow(scrollWidth: number, clientWidth: number): void {
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(scrollWidth)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(clientWidth)
}

/** A ResizeObserver that reports nothing until a test says one of the elements it watches resized. */
const watchers: { callback: () => void; targets: Set<Element> }[] = []

class FakeResizeObserver {
  private readonly watcher: { callback: () => void; targets: Set<Element> }

  constructor(callback: () => void) {
    this.watcher = { callback, targets: new Set() }
    watchers.push(this.watcher)
  }

  observe(target: Element): void {
    this.watcher.targets.add(target)
  }

  disconnect(): void {
    this.watcher.targets.clear()
  }
}

function resize(target: Element): void {
  act(() => {
    for (const watcher of watchers) {
      if (watcher.targets.has(target)) watcher.callback()
    }
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  watchers.length = 0
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

  it('becomes focusable when its content grows past a wrapper that did not resize', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    stubOverflow(300, 300)
    render(
      <ScrollRegion label="Tickets">
        <table data-testid="wide" />
      </ScrollRegion>,
    )
    expect(screen.queryByRole('region')).not.toBeInTheDocument()

    stubOverflow(900, 300)
    resize(screen.getByTestId('wide'))

    expect(screen.getByRole('region', { name: 'Tickets' })).toHaveAttribute('tabindex', '0')
  })

  it('drops the tab stop again when the content shrinks back to fit', () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    stubOverflow(900, 300)
    render(
      <ScrollRegion label="Tickets">
        <table data-testid="wide" />
      </ScrollRegion>,
    )
    expect(screen.getByRole('region', { name: 'Tickets' })).toBeInTheDocument()

    stubOverflow(300, 300)
    resize(screen.getByTestId('wide'))

    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })

  it('watches content that is added after the first render', async () => {
    vi.stubGlobal('ResizeObserver', FakeResizeObserver)
    stubOverflow(300, 300)
    const { rerender } = render(<ScrollRegion label="Tickets">{null}</ScrollRegion>)

    rerender(
      <ScrollRegion label="Tickets">
        <table data-testid="late" />
      </ScrollRegion>,
    )
    await screen.findByTestId('late')
    await act(async () => {
      await Promise.resolve()
    })
    stubOverflow(900, 300)
    resize(screen.getByTestId('late'))

    expect(screen.getByRole('region', { name: 'Tickets' })).toBeInTheDocument()
  })
})
