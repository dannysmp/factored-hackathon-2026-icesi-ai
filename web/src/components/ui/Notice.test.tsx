/** Component test: the notice's roles, icon and accessibility for every tone. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { Notice } from './Notice'

describe('Notice', () => {
  it('announces a failure at once when the caller gives it the alert role', () => {
    render(
      <Notice tone="error" role="alert">
        No se pudo cargar.
      </Notice>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('No se pudo cargar.')
  })

  it('announces a result politely when the caller gives it the status role', () => {
    render(
      <Notice tone="success" role="status">
        Listo.
      </Notice>,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Listo.')
  })

  it('is plain page content without a role when the caller gives none', () => {
    render(<Notice tone="info">Nota</Notice>)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByText('Nota')).toBeInTheDocument()
  })

  it.each(['info', 'success', 'warning', 'error'] as const)(
    'carries its own tone class, %s, and no other',
    (tone) => {
      const { container } = render(<Notice tone={tone}>Mensaje</Notice>)

      const { className } = container.firstElementChild as HTMLElement
      const others = (['info', 'success', 'warning', 'error'] as const).filter((t) => t !== tone)
      expect(className).toMatch(new RegExp(tone))
      for (const other of others) expect(className).not.toMatch(new RegExp(other))
    },
  )

  it.each(['info', 'success', 'warning', 'error'] as const)(
    'carries a decorative icon and no accessibility violations for the %s tone',
    async (tone) => {
      const { container } = render(<Notice tone={tone}>Mensaje</Notice>)

      expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
      expect(await axe(container)).toHaveNoViolations()
    },
  )
})
