/** Component test: the sign-in screen's demonstration notice stays visible and cannot be dismissed. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInScreen } from './SignInScreen'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'

const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'bruno', display_name: 'Bruno', language: 'pt', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SignInScreen demonstration notice', () => {
  it('is shown before any persona is chosen and offers no way to dismiss it', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByText(es['signin.intro'])).toBeInTheDocument()
    // The only control that is a button is the sign-in submit; nothing closes the notice.
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button', { name: es['signin.submit'] })).toBeInTheDocument()
  })

  it.each([
    ['ana', es],
    ['bruno', pt],
    ['emma', en],
  ] as const)(
    'follows the language of the chosen persona (%s) and stays',
    async (slug, catalog) => {
      vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
      const user = userEvent.setup()
      render(<SignInScreen onSignedIn={vi.fn()} />)
      await screen.findByLabelText(es['signin.personaLabel'])

      await user.selectOptions(screen.getByLabelText(es['signin.personaLabel']), slug)

      expect(screen.getByText(catalog['signin.intro'])).toBeInTheDocument()
    },
  )

  it('stays after a refused sign-in, so a retry is made with the notice still in view', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new api.SignInError(401, 'Wrong access code'))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByLabelText(es['signin.personaLabel'])

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'wrong')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    await screen.findByRole('alert')
    expect(screen.getByText(es['signin.intro'])).toBeInTheDocument()
  })
})
