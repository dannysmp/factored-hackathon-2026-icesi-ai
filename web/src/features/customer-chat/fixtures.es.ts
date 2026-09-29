/**
 * A scripted Spanish conversation, in `TurnResponse` shape.
 *
 * Team-generated, not derived from a real customer, on the same terms as `FILE_DISPUTE_EN`
 * (`fixtures.ts`): it exists to prove the chat UI end to end before the live endpoint drives a
 * real conversation. Unlike that one, every reply line here is copied verbatim from
 * `docs/demo-scripts.md`'s "Normal path in Spanish" script, itself the template renderer's real,
 * unedited output for the recorded envelope in `tests/fixtures/scripted_flows.es.json` — so this
 * fixture renders the same grounded Spanish text a live session produces for this scenario, not
 * an approximation of it.
 */
import type { TurnResponse } from './contracts'

const REFERENCE_DATE_LINE = 'Fecha de referencia de los datos: 18 de junio de 2026'
const DEMO_NOTICE = 'Esta es una sesión de demostración con datos sintéticos.'

export const FILE_DISPUTE_ES: readonly TurnResponse[] = [
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0001',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 1,
    lang: 'es',
    reply: '¿Podría decirme el comercio, el monto o la fecha del cargo que quiere disputar?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'transaction',
    end_session: false,
    handoff_ticket: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0002',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 2,
    lang: 'es',
    reply:
      'Encontré una transacción de 250,00 MXN en Tienda Sol el 12 de junio de 2026. ¿Es esta la que quiere disputar?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [{ number: 1, label: '250,00 MXN en Tienda Sol el 12 de junio de 2026' }],
    next_expected: 'transaction_choice',
    end_session: false,
    handoff_ticket: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0003',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 3,
    lang: 'es',
    reply:
      '¿Cuál es el motivo? Puede ser un cargo no reconocido, un cargo duplicado, un monto incorrecto, un servicio no recibido o un fraude.',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'reason',
    end_session: false,
    handoff_ticket: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0004',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 4,
    lang: 'es',
    reply:
      'Voy a presentar una disputa por cargo no reconocido sobre el cargo de 250,00 MXN del 12 de junio de 2026. Un asesor la revisará; esto no garantiza un resultado. ¿Confirma que desea presentarla?',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: 'confirmation',
    end_session: false,
    handoff_ticket: null,
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0005',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 5,
    lang: 'es',
    reply:
      'Su disputa quedó registrada con el número de caso D-2001. Espere una primera respuesta antes del 25 de junio de 2026.',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: null,
    end_session: false,
    handoff_ticket: 'D-2001',
  },
  {
    contract_version: '1',
    turn_id: 'fixture-turn-es-0006',
    conversation_id: 'fixture-conversation-es-0001',
    state_version: 6,
    lang: 'es',
    reply: 'Gracias por escribir. Que tenga un buen día.',
    reference_date_line: REFERENCE_DATE_LINE,
    demo_notice: DEMO_NOTICE,
    choices: [],
    next_expected: null,
    end_session: true,
    handoff_ticket: null,
  },
] as const
