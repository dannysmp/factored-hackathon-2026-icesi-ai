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
 * One case's whole detail (AC-E10-02, AC-E10-03): the packet and the timeline, as two genuine
 * `Tabs` panels — two complete, independent views of the same case, the fit ADR-19's tabs
 * primitive was tested for. Every state rendered deliberately (AC-E10-18): loading, error with a
 * retry, a case that no longer resolves, and the ready view.
 *
 * Fixed Spanish copy, not a catalog entry (D91), the same as `QueueScreen`.
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
      <Button variant="quiet" onClick={onBack}>
        Volver a la cola
      </Button>
      <ReferenceBanner
        referenceDateLine={`Fecha de referencia de los datos: ${formatDate(detail.packet.reference_date)}.`}
        demoNotice="Esta es una sesión de demostración."
      />
      <h2>
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
