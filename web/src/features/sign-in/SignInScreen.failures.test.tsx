/** Component test: what the sign-in screen says, and lets the person do, when something fails. */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { SignInError } from './api'
import { SignInScreen } from './SignInScreen'
import { es } from '../../i18n/es'
import { pt } from '../../i18n/pt'
import { findPersonaRadio } from './personaRadios'

/** Two customer personas, one speaking Spanish and one Portuguese. */
const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'joao', display_name: 'João', language: 'pt', audience: 'customer' as const },
]
/** The same personas plus a second Portuguese speaker, for preferred-persona selection. */
const TWO_IN_PORTUGUESE = [
  ...PERSONAS,
  { slug: 'beatriz', display_name: 'Beatriz', language: 'pt', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

/** Types `code` into the access code field and presses the sign-in button. */
async function submitCode(user: ReturnType<typeof userEvent.setup>, code: string): Promise<void> {
  await user.type(await screen.findByLabelText(es['signin.accessCodeLabel']), code)
  await user.click(screen.getByRole('button', { name: es['signin.submit'] }))
}

describe('SignInScreen when the persona directory fails', () => {
  it.each([
    [new SignInError(503, 'Unavailable'), es['failure.unavailable']],
    [new SignInError(429, 'Too many'), es['failure.rateLimited']],
    [new TypeError('Failed to fetch'), es['failure.offline']],
    [new DOMException('timed out', 'TimeoutError'), es['failure.timeout']],
  ])('says why it could not load, and offers Retry (%#)', async (error, reason) => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(error)
    const { container } = render(<SignInScreen onSignedIn={vi.fn()} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(es['signin.unreachable'])
    expect(alert).toHaveTextContent(reason)
    expect(screen.getByRole('button', { name: es['common.retry'] })).toBeInTheDocument()
    expect(await axe(container)).toHaveNoViolations()
  })

  it('adds no reason when the failure is not one it can name', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockRejectedValue(new Error('odd'))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      `${es['signin.unreachable']}${es['common.retry']}`,
    )
  })

  it('loads the directory again when Retry is pressed and shows the form once it answers', async () => {
    const user = userEvent.setup()
    const fetcher = vi
      .spyOn(api, 'fetchCustomerPersonas')
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: es['common.retry'] }))

    expect(
      await screen.findByRole('group', { name: es['signin.personaGroupLabel'] }),
    ).toBeInTheDocument()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the loading text again while a retry is in flight', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'fetchCustomerPersonas')
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockReturnValueOnce(new Promise(() => undefined))
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: es['common.retry'] }))

    expect(screen.getByRole('status')).toHaveTextContent(es['signin.loading'])
  })
})

describe('SignInScreen when the sign-in is refused', () => {
  it.each([
    [new SignInError(401, 'Sign-in refused'), es['signin.refused']],
    [new SignInError(429, 'Too many'), es['failure.rateLimited']],
    [new SignInError(503, 'Unavailable'), es['failure.unavailable']],
    [new TypeError('Failed to fetch'), es['failure.offline']],
    [new DOMException('timed out', 'TimeoutError'), es['failure.timeout']],
    [new Error('odd'), es['common.error.generic']],
  ])('says what happened in the persona’s language (%#)', async (error, message) => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(error)
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await submitCode(user, 'a-code')

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
  })

  it('speaks the selected persona’s language, not the default one', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new SignInError(429, 'Too many'))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await user.click(await findPersonaRadio('joao'))
    await user.type(screen.getByLabelText(pt['signin.accessCodeLabel']), 'a-code')
    await user.click(screen.getByRole('button', { name: pt['signin.submit'] }))

    expect(await screen.findByRole('alert')).toHaveTextContent(pt['failure.rateLimited'])
  })

  it('returns the keyboard to the access code and ties the message to it', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockRejectedValue(new SignInError(401, 'Sign-in refused'))
    const user = userEvent.setup()
    const { container } = render(<SignInScreen onSignedIn={vi.fn()} />)

    await submitCode(user, 'wrong')

    const field = screen.getByLabelText(es['signin.accessCodeLabel'])
    await waitFor(() => {
      expect(field).toHaveFocus()
    })
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAccessibleDescription(es['signin.refused'])
    expect(await axe(container)).toHaveNoViolations()
  })

  it('clears the message when the person tries again', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn')
      .mockRejectedValueOnce(new SignInError(401, 'Sign-in refused'))
      .mockResolvedValueOnce('token')
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await submitCode(user, 'wrong')
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    await waitFor(() => {
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })

  it('says it is signing in, and locks the form, while the request is out', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    vi.spyOn(api, 'signIn').mockReturnValue(new Promise(() => undefined))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await submitCode(user, 'a-code')

    const button = await screen.findByRole('button', { name: es['signin.submitting'] })
    expect(button).toBeDisabled()
    expect(screen.getByLabelText(es['signin.accessCodeLabel'])).toBeDisabled()
    expect(screen.getByRole('group', { name: es['signin.personaGroupLabel'] })).toBeDisabled()
  })
})

describe('SignInScreen form', () => {
  it('labels each persona with the language they speak', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { name: /^Ana\s+Español\b/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^João\s+Portugués\b/ })).toBeInTheDocument()
  })

  it('shows a persona whose language is unknown by name alone', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue([
      { slug: 'zora', display_name: 'Zora', language: 'klingon', audience: 'customer' as const },
    ])
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { name: 'Zora' })).toBeInTheDocument()
  })

  it('explains why the button is off until an access code is typed', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    const field = await screen.findByLabelText(es['signin.accessCodeLabel'])
    const submit = screen.getByRole('button', { name: es['signin.submit'] })
    expect(submit).toBeDisabled()
    expect(field).toHaveAccessibleDescription(es['signin.accessCodeHint'])

    await user.type(field, 'a-code')

    expect(submit).toBeEnabled()
    expect(screen.queryByText(es['signin.accessCodeHint'])).not.toBeInTheDocument()
  })

  it('keeps the button off for a code made only of spaces', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await user.type(await screen.findByLabelText(es['signin.accessCodeLabel']), '   ')

    expect(screen.getByRole('button', { name: es['signin.submit'] })).toBeDisabled()
    expect(screen.getByText(es['signin.accessCodeHint'])).toBeInTheDocument()
  })

  it('asks the browser not to save or correct the access code', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    const field = await screen.findByLabelText(es['signin.accessCodeLabel'])
    expect(field).toHaveAttribute('name', 'access-code')
    expect(field).toHaveAttribute('autocomplete', 'off')
    expect(field).toHaveAttribute('spellcheck', 'false')
  })

  it('names the card by its own heading', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
    expect(screen.getByRole('region', { name: es['signin.productName'] })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 2, name: es['signin.productName'] }),
    ).toBeInTheDocument()
  })

  it('moves the keyboard to the persona picker when asked to', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen focusForm onSignedIn={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByRole('radio', { checked: true })).toHaveFocus()
    })
  })

  it('leaves the keyboard where it is when not asked to move it', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen onSignedIn={vi.fn()} />)

    await screen.findByRole('group', { name: es['signin.personaGroupLabel'] })
    expect(screen.getByRole('radio', { checked: true })).not.toHaveFocus()
  })

  it('selects the first persona who speaks the preferred language, and speaks it', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen preferredLang="pt" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'joao')
  })

  it('selects the preferred persona when they speak the preferred language', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(TWO_IN_PORTUGUESE)
    render(<SignInScreen preferredLang="pt" preferredSlug="beatriz" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'beatriz')
  })

  it('ignores the preferred persona when they do not speak the preferred language', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(TWO_IN_PORTUGUESE)
    render(<SignInScreen preferredLang="pt" preferredSlug="ana" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'joao')
  })

  it('selects the first persona when none speaks the preferred language', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(PERSONAS)
    render(<SignInScreen preferredLang="en" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('radio', { checked: true })).toHaveAttribute('value', 'ana')
  })
})
