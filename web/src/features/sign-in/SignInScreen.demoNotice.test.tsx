/** Component test: the sign-in screen's demonstration notice stays visible and cannot be dismissed. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInScreen } from './SignInScreen'
import { en } from '../../i18n/en'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { getPersonaRadio } from './personaRadios'

/** One customer persona per language, so the notice is checked in each. */
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
    // None of the buttons closes the notice: they switch language, show the code and submit.
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Español',
      'Português',
      'English',
      es['signin.accessCodeShow'],
      es['signin.submit'],
    ])
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
      await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })

      await user.click(getPersonaRadio(slug))

      expect(screen.getByText(catalog['signin.intro'])).toBeInTheDocument()
    },
  )

  it('stays after a refused sign-in, so a retry is made with the notice still in view', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new api.SignInError(401, 'Wrong access code'))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'wrong')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    await screen.findByRole('alert')
    expect(screen.getByText(es['signin.intro'])).toBeInTheDocument()
  })

  it('is shown on the agent console sign-in too, with no way to dismiss it', async () => {
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue([
      { slug: 'diego', display_name: 'Diego', language: 'es', audience: 'agent' as const },
    ])
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByText(es['signin.intro'])).toBeInTheDocument()
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual([
      es['signin.accessCodeShow'],
      es['signin.submit'],
    ])
  })
})
