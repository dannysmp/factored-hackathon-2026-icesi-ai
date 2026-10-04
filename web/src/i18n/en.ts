// Structural placeholders: real copy for the chat and sign-in screens migrates in separately,
// through the language review each catalog's strings go through before they ship.
import type { Messages } from './messages'

export const en: Messages = {
  'common.loading': 'Loading…',
  'common.retry': 'Retry',
  'common.error.generic': 'Something went wrong.',
  'chat.placeholder': 'Type your message',
  'chat.messageLabel': 'Your message',
  'chat.starting': 'Starting the conversation…',
  'chat.couldNotStart': 'The conversation could not start. Please try again.',
  'chat.couldNotSend': 'Your last message could not be sent. Please try again.',
  'chat.noMessagesYet': 'No messages yet.',
  'chat.assistantLabel': 'Assistant:',
  'chat.customerLabel': 'You:',
  'chat.confirm': 'Confirm',
  'chat.send': 'Send',
  'chat.ended': 'This conversation has ended.',
  'chat.caseReference': 'Case reference: {ticket}.',
  'signin.loading': 'Loading the demonstration sign-in…',
  'signin.unreachable': 'The demonstration sign-in could not be reached. Please try again.',
  'signin.unavailable': 'The demonstration is not available at the moment.',
  'signin.intro': 'This is a demonstration. Sign in with one of the personas below.',
  'signin.personaLabel': 'Persona',
  'signin.accessCodeLabel': 'Access code',
  'signin.refused': 'The access code or persona was refused. Please try again.',
  'signin.submit': 'Sign in',
}
