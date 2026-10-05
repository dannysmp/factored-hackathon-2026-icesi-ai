/**
 * Every user-facing string key the catalogs must carry, one flat, namespaced key per string
 * (e.g. `chat.send`). The type checker enforces that every catalog defines every key;
 * `catalogs.test.ts` additionally proves no catalog leaves a key with a blank value, something
 * the type checker can't express.
 */
export interface Messages {
  'app.title': string
  'app.signOut': string
  'app.sessionExpired': string
  'common.retry': string
  'common.error.generic': string
  'failure.offline': string
  'failure.timeout': string
  'failure.rateLimited': string
  'failure.unavailable': string
  'chat.regionLabel': string
  'chat.messagesLabel': string
  'chat.messageLabel': string
  'chat.starting': string
  'chat.couldNotStart': string
  'chat.couldNotSend': string
  'chat.noMessagesYet': string
  'chat.assistantLabel': string
  'chat.customerLabel': string
  'chat.confirm': string
  'chat.send': string
  'chat.assistantTyping': string
  'chat.notSent': string
  /** `{count}` is replaced with the number of characters the customer may still type. */
  'chat.charactersLeft': string
  'chat.ended': string
  /** `{ticket}` is replaced with the handoff ticket at render time. */
  'chat.caseReference': string
  'signin.regionLabel': string
  'signin.loading': string
  'signin.unreachable': string
  'signin.unavailable': string
  'signin.intro': string
  'signin.personaLabel': string
  'signin.accessCodeLabel': string
  'signin.refused': string
  'signin.accessCodeHint': string
  'signin.submit': string
  'signin.submitting': string
}
