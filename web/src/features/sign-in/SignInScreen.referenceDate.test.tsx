/** Component test: the sign-in screen states the date the data is as of, as the service words it. */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { axe } from 'jest-axe'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { es } from '../../i18n/es'
import { SignInScreen } from './SignInScreen'
import { REFERENCE_DATE_LINES, directoryOf } from './personaDirectory'
import { getPersonaRadio } from './personaRadios'

const PERSONAS = [
  { slug: 'ana', display_name: 'Ana', language: 'es', audience: 'customer' as const },
  { slug: 'bruno', display_name: 'Bruno', language: 'pt', audience: 'customer' as const },
  { slug: 'emma', display_name: 'Emma', language: 'en', audience: 'customer' as const },
]

afterEach(() => {
  vi.restoreAllMocks()
})

describe('SignInScreen reference date', () => {
  it.each([
    ['ana', REFERENCE_DATE_LINES.es],
    ['bruno', REFERENCE_DATE_LINES.pt],
    ['emma', REFERENCE_DATE_LINES.en],
  ] as const)('is shown in the language of the chosen persona (%s)', async (slug, line) => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('note')

    await user.click(getPersonaRadio(slug))

    const notes = screen.getAllByRole('note')
    expect(notes).toHaveLength(1)
    expect(notes[0]).toHaveTextContent(line)
  })

  it('shows exactly the text the service sent, never a date of its own', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(
      directoryOf(PERSONAS, {
        es: 'Línea del servicio',
        pt: 'Linha do serviço',
        en: 'Line from the service',
      }),
    )
    render(<SignInScreen onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('note')).toHaveTextContent(/^Línea del servicio$/)
  })

  it('switches with the language buttons, like the rest of the screen', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('note')

    await user.click(screen.getByRole('button', { name: 'Português' }))
    expect(screen.getByRole('note')).toHaveTextContent(REFERENCE_DATE_LINES.pt)

    await user.click(screen.getByRole('button', { name: 'English' }))
    expect(screen.getByRole('note')).toHaveTextContent(REFERENCE_DATE_LINES.en)
  })

  it('is shown on the agent sign-in, in Spanish', async () => {
    vi.spyOn(api, 'fetchAgentPersonas').mockResolvedValue(
      directoryOf([
        { slug: 'agent-beatriz', display_name: 'Beatriz', language: 'pt', audience: 'agent' },
      ]),
    )
    render(<SignInScreen audience="agent" onSignedIn={vi.fn()} />)

    expect(await screen.findByRole('note')).toHaveTextContent(REFERENCE_DATE_LINES.es)
  })

  it('stays after a refused sign-in', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    vi.spyOn(api, 'signIn').mockRejectedValue(new api.SignInError(401, 'Wrong access code'))
    const user = userEvent.setup()
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('note')

    await user.type(screen.getByLabelText(es['signin.accessCodeLabel']), 'wrong')
    await user.click(screen.getByRole('button', { name: es['signin.submit'] }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(REFERENCE_DATE_LINES.es)
  })

  it('is not shown while the directory is loading or when it cannot be reached', async () => {
    const fetcher = vi.spyOn(api, 'fetchCustomerPersonas')
    fetcher.mockReturnValueOnce(new Promise(() => undefined))
    const { unmount } = render(<SignInScreen onSignedIn={vi.fn()} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    unmount()

    fetcher.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('has no automatically detectable accessibility violations', async () => {
    vi.spyOn(api, 'fetchCustomerPersonas').mockResolvedValue(directoryOf(PERSONAS))
    render(<SignInScreen onSignedIn={vi.fn()} />)
    await screen.findByRole('note')

    expect(await axe(document.body)).toHaveNoViolations()
  })
})
