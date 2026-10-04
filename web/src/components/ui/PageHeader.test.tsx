/** Component test: the page header's landmark, heading and actions slot. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { PageHeader } from './PageHeader'

describe('PageHeader', () => {
  it('is the page banner and carries the one level-one heading', () => {
    render(<PageHeader title="Consola del agente" />)

    expect(screen.getByRole('banner')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 1, name: 'Consola del agente' }),
    ).toBeInTheDocument()
  })

  it('renders page-level actions on the right when given', () => {
    render(
      <PageHeader title="Dispute intake">
        <button type="button">Sign out</button>
      </PageHeader>,
    )

    expect(screen.getByRole('banner')).toContainElement(
      screen.getByRole('button', { name: 'Sign out' }),
    )
  })

  it('renders no empty actions wrapper without children', () => {
    const { container } = render(<PageHeader title="Dispute intake" />)

    expect(container.querySelectorAll('header > div > div')).toHaveLength(1)
  })

  it('hides the decorative mark from assistive technology', () => {
    const { container } = render(<PageHeader title="Dispute intake" />)

    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it.each(['narrow', 'wide'] as const)(
    'has no accessibility violations at %s width',
    async (width) => {
      const { container } = render(
        <>
          <PageHeader title="Dispute intake" width={width} />
          <main>Content</main>
        </>,
      )

      expect(await axe(container)).toHaveNoViolations()
    },
  )
})
