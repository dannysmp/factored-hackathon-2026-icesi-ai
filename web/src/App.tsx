import type { JSX } from 'react'
import { ChatFeature } from './features/customer-chat/ChatFeature'
import { FixtureChatClient } from './features/customer-chat/client'
import { FILE_DISPUTE_EN } from './features/customer-chat/fixtures'

// Built once, at module scope: a client is a resource, not something to recreate on every
// render (frontend standard, section 3). The live client is the next slice's; this app has only
// the fixture one until the demonstration sign-in broker and the conversation store exist.
const client = new FixtureChatClient(FILE_DISPUTE_EN)

/**
 * The app shell: the customer chat, replayed against a scripted fixture conversation.
 */
export function App(): JSX.Element {
  return (
    <main>
      <h1>Dispute intake</h1>
      <ChatFeature client={client} />
    </main>
  )
}
