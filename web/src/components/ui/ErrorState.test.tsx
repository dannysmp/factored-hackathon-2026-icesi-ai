/** Component test: the shared error state says what failed, why, and what can be done. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { ErrorState } from './ErrorState'

describe('ErrorState', () => {
  it('is announced as an alert carrying the title, the reason and the actions in that order', () => {
    render(
      <ErrorState title="Your last message could not be sent." reason="There is no connection.">
        <button type="button">Retry</button>
      </ErrorState>,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(
      'Your last message could not be sent.There is no connection.Retry',
    )
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('leaves out the reason and the actions when there are none', () => {
    const { container } = render(<ErrorState title="Something went wrong." />)

    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong.')
    expect(container.querySelectorAll('p')).toHaveLength(1)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations', async () => {
    const { container } = render(
      <ErrorState title="Title." reason="Reason.">
        <button type="button">Retry</button>
      </ErrorState>,
    )
    expect(await axe(container)).toHaveNoViolations()
  })
})
