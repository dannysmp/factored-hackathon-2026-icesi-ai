/**
 * The agent console's ticket-detail screen: one case's handoff packet and audit timeline, in
 * separate tabs, with its loading, error and not-found states.
 */
import { useEffect, useRef } from 'react'
import type { JSX } from 'react'
import './TicketDetailScreen.css'
import { ReferenceBanner } from '../customer-chat/components/ReferenceBanner'
import { Button } from '../../components/ui/Button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/Tabs'
import { PacketPanel } from './components/PacketPanel'
import { TimelinePanel } from './components/TimelinePanel'
import type { TicketDetailClient } from './ticketDetailClient'
import { formatDate } from './format'
import { TRIGGER_LABELS } from './labels'
import { useTicketDetail } from './useTicketDetail'

/**
 * One case's whole detail: the packet and the timeline, as two genuine `Tabs` panels — two
 * complete, independent views of the same case. Every state is rendered deliberately: loading,
 * error with retry and back actions, a case that does not resolve, and the ready view. Never
 * renders a document number; the packet carries only a first name and a masked id.
 *
 * Fixed Spanish copy, not a catalog entry, the same as `QueueScreen`: the console never uses the
 * per-language catalogs. `onBack` returns to the queue; `onSessionExpired` is forwarded to the
 * loader so a 401 sends the agent back to sign-in.
 *
 * Opening a case from the queue removes the button that was pressed, so when the case is ready and
 * nothing holds focus it moves to the case heading, where a keyboard or screen-reader user starts
 * reading the case. A focus the person already placed elsewhere is never taken.
 */
export function TicketDetailScreen({
  client,
  ticketRef,
  onBack,
  onSessionExpired,
}: {
  client: TicketDetailClient
  ticketRef: string
  onBack: () => void
  onSessionExpired?: () => void
}): JSX.Element {
  const query = useTicketDetail(client, ticketRef, onSessionExpired)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const ready = query.status === 'ready'

  useEffect(() => {
    if (ready && document.activeElement === document.body) headingRef.current?.focus()
  }, [ready])

  if (query.status === 'error') {
    return (
      <div className="ticket-detail-screen" role="alert">
        <p>No se pudo cargar el caso. Intente de nuevo.</p>
        <Button variant="primary" onClick={query.retry}>
          Intentar de nuevo
        </Button>
        <Button onClick={onBack}>Volver a la cola</Button>
      </div>
    )
  }

  if (query.status === 'loading') {
    return (
      <div className="ticket-detail-screen">
        <p aria-live="polite" role="status">
          Cargando el caso…
        </p>
      </div>
    )
  }

  if (query.status === 'not_found') {
    return (
      <div className="ticket-detail-screen">
        <p>Este caso ya no está disponible.</p>
        <Button onClick={onBack}>Volver a la cola</Button>
      </div>
    )
  }

  const { detail } = query
  if (detail === null) {
    // Unreachable: `status === 'ready'` only ever follows a non-null `detail` (`useTicketDetail`
    // sets both together) — narrows the type for the render below without an assertion.
    return (
      <div className="ticket-detail-screen">
        <p>Este caso ya no está disponible.</p>
      </div>
    )
  }

  return (
    <section className="ticket-detail-screen" aria-label="Detalle del caso">
      <Button variant="quiet" className="ticket-back" onClick={onBack}>
        Volver a la cola
      </Button>
      <ReferenceBanner
        referenceDateLine={`Fecha de referencia de los datos: ${formatDate(detail.packet.reference_date)}.`}
        demoNotice="Esta es una sesión de demostración."
      />
      <h2 ref={headingRef} tabIndex={-1}>
        Caso <span className="ticket-ref">{detail.item.ticket_ref}</span>
      </h2>
      <p className="ticket-trigger">{TRIGGER_LABELS[detail.item.trigger]}</p>

      <Tabs defaultValue="packet">
        <TabsList aria-label="Secciones del caso">
          <TabsTrigger value="packet">Paquete</TabsTrigger>
          <TabsTrigger value="timeline">Cronología</TabsTrigger>
        </TabsList>
        <TabsContent value="packet">
          <PacketPanel packet={detail.packet} />
        </TabsContent>
        <TabsContent value="timeline">
          <TimelinePanel entries={detail.timeline} />
        </TabsContent>
      </Tabs>
    </section>
  )
}
