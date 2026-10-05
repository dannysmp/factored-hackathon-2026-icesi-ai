// English catalog, for a bank customer: plain, courteous second person, and "profile" for the
// demonstration identities rather than the internal term for them. Every key in `Messages` must be
// present and non-blank, and every `{placeholder}` must survive; the catalog tests enforce both.
import type { Messages } from './messages'

/** Every interface message in English. */
export const en: Messages = {
  'app.title': 'Transaction disputes',
  'app.signOut': 'Sign out',
  'app.sessionExpired': 'Your session has expired. Please sign in again.',
  'common.retry': 'Retry',
  'common.error.generic': 'Something went wrong.',
  'failure.offline':
    'There is no connection to the service. Check your internet connection and try again.',
  'failure.timeout': 'The response took too long. Please try again.',
  'failure.rateLimited': 'Too many attempts. Please wait a minute and try again.',
  'failure.unavailable': 'The service is unavailable right now. Please try again in a few minutes.',
  'chat.regionLabel': 'Conversation with the assistant',
  'chat.messagesLabel': 'Messages',
  'chat.messageLabel': 'Your message',
  'chat.starting': 'Starting the conversation…',
  'chat.couldNotStart': 'The conversation could not start.',
  'chat.couldNotSend': 'Your last message could not be sent.',
  'chat.noMessagesYet': 'No messages yet.',
  'chat.assistantLabel': 'Assistant:',
  'chat.customerLabel': 'You:',
  'chat.confirm': 'Confirm',
  'chat.send': 'Send',
  'chat.assistantTyping': 'The assistant is typing…',
  'chat.notSent': 'Not sent',
  'chat.charactersLeft': 'Characters left: {count}',
  'chat.result.filedTitle': 'Dispute filed',
  'chat.result.escalatedTitle': 'A person will review your request',
  'chat.result.closedTitle': 'Conversation ended',
  'chat.result.caseNumberLabel': 'Case reference',
  'chat.result.keepNumber': 'Keep this number for any question about your request.',
  'signin.regionLabel': 'Demonstration sign-in',
  'signin.loading': 'Loading the demonstration sign-in…',
  'signin.unreachable': 'The demonstration sign-in could not be loaded.',
  'signin.unavailable': 'The demonstration is not available at the moment.',
  'signin.intro': 'This is a demonstration. Sign in with one of the profiles below.',
  'signin.personaLabel': 'Profile',
  'signin.accessCodeLabel': 'Access code',
  'signin.refused': 'The access code or profile was not accepted. Please try again.',
  'signin.accessCodeHint': 'Enter the access code to continue.',
  'signin.submit': 'Sign in',
  'signin.submitting': 'Signing in…',
}
