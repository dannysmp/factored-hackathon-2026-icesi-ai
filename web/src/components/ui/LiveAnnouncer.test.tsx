/** Component test: the announcer is a polite status region that is present before it has text. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { LiveAnnouncer } from './LiveAnnouncer'

describe('LiveAnnouncer', () => {
  it('is a polite status region even while it is empty', () => {
    render(<LiveAnnouncer message="" />)

    const region = screen.getByRole('status')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toBeEmptyDOMElement()
  })

  it('carries the message it is given and replaces it when the message changes', () => {
    const { rerender } = render(<LiveAnnouncer message="Primera respuesta" />)
    expect(screen.getByRole('status')).toHaveTextContent('Primera respuesta')

    rerender(<LiveAnnouncer message="Segunda respuesta" />)

    expect(screen.getByRole('status')).toHaveTextContent('Segunda respuesta')
    expect(screen.queryByText('Primera respuesta')).not.toBeInTheDocument()
  })

  it('replaces the text node when the key changes even though the words repeat', () => {
    const { rerender } = render(<LiveAnnouncer message="Again" messageKey="turn-1" />)
    const region = screen.getByRole('status')
    const first = region.firstChild

    rerender(<LiveAnnouncer message="Again" messageKey="turn-1" />)
    expect(region.firstChild).toBe(first)

    rerender(<LiveAnnouncer message="Again" messageKey="turn-2" />)
    expect(region.firstChild).not.toBe(first)
    expect(region).toHaveTextContent('Again')
  })

  it('has no accessibility violations', async () => {
    const { container } = render(<LiveAnnouncer message="Hola" />)

    expect(await axe(container)).toHaveNoViolations()
  })
})
