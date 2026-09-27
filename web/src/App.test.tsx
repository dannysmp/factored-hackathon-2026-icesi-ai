/** Component test: the app shell renders and hosts the chat with no accessibility violations. */
import { render, screen } from '@testing-library/react'
import { axe } from 'jest-axe'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App', () => {
  it('renders the placeholder heading and the chat', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Dispute intake' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Customer chat' })).toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations', async () => {
    const { container } = render(<App />)
    await screen.findByText(/which transaction/i)
    expect(await axe(container)).toHaveNoViolations()
  })
})
