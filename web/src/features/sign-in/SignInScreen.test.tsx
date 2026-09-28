/** Component test: the sign-in screen's four states and its accessibility. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInScreen } from './SignInScreen'

const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SignInScreen', () => {
  it('shows the persona picker once the directory loads', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByLabelText('Persona')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Ana' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Emma' })).toBeInTheDocument()
  })

  it('shows a retryable error when the directory cannot be fetched', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new Error('network down'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The demonstration sign-in could not be reached',
    )
  })

  it('signs in with the selected persona and access code, then calls onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const signInMock = vi.spyOn(api, 'signIn').mockResolvedValue('token-abc')
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByLabelText('Persona')
    await user.selectOptions(screen.getByLabelText('Persona'), 'emma')
    await user.type(screen.getByLabelText('Access code'), 'the-code')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(signInMock).toHaveBeenCalledWith('emma', 'the-code')
    await screen.findByRole('button', { name: 'Sign in' })
    expect(onSignedIn).toHaveBeenCalledWith('token-abc', 'en')
  })

  it('shows a refusal message and lets the customer retry, without calling onSignedIn', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new Error('refused'))
    const onSignedIn = vi.fn()
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={onSignedIn} />)

    await screen.findByLabelText('Persona')
    await user.type(screen.getByLabelText('Access code'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('refused')
    expect(onSignedIn).not.toHaveBeenCalled()
  })

  it('has no automatically detectable accessibility violations once ready', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const { container } = render(<SignInScreen onSignedIn={vi.fn()} />)

    await screen.findByLabelText('Persona')
    expect(await axe(container)).toHaveNoViolations()
  })
})
