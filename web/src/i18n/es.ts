// Spanish catalog, for a bank customer across Latin America: the formal usted throughout, neutral
// vocabulary, and a first-person "Yo" for the customer's own messages. Every key in `Messages`
// must be present and non-blank, and every `{placeholder}` must survive; the catalog tests enforce
// both, and also that no informal address creeps in.
import type { Messages } from './messages'

/** Every interface message in Spanish. */
export const es: Messages = {
  'app.title': 'Disputa de transacciones',
  'app.signOut': 'Cerrar sesión',
  'app.sessionExpired': 'Su sesión expiró. Inicie sesión de nuevo.',
  'common.retry': 'Reintentar',
  'common.error.generic': 'Ocurrió un error.',
  'failure.offline':
    'No hay conexión con el servicio. Revise su conexión a internet e inténtelo de nuevo.',
  'failure.timeout': 'La respuesta tardó demasiado. Inténtelo de nuevo.',
  'failure.rateLimited': 'Ha realizado demasiados intentos. Espere un minuto e inténtelo de nuevo.',
  'failure.unavailable':
    'El servicio no está disponible por ahora. Inténtelo de nuevo en unos minutos.',
  'chat.regionLabel': 'Conversación con el asistente',
  'chat.messagesLabel': 'Mensajes',
  'chat.messageLabel': 'Su mensaje',
  'chat.starting': 'Iniciando la conversación…',
  'chat.couldNotStart': 'No se pudo iniciar la conversación.',
  'chat.couldNotSend': 'No se pudo enviar su último mensaje.',
  'chat.noMessagesYet': 'Aún no hay mensajes.',
  'chat.assistantLabel': 'Asistente:',
  'chat.customerLabel': 'Yo:',
  'chat.confirm': 'Confirmar',
  'chat.send': 'Enviar',
  'chat.assistantTyping': 'El asistente está escribiendo…',
  'chat.notSent': 'Mensaje no enviado',
  'chat.charactersLeft': 'Caracteres restantes: {count}',
  'chat.result.filedTitle': 'Disputa registrada',
  'chat.result.escalatedTitle': 'Un asesor revisará su solicitud',
  'chat.result.closedTitle': 'Conversación finalizada',
  'chat.result.caseNumberLabel': 'Número de caso',
  'chat.result.keepNumber': 'Conserve este número para cualquier consulta sobre su solicitud.',
  'chat.result.filedEarlierLabel': 'Disputa registrada antes, número de caso',
  'signin.regionLabel': 'Inicio de sesión de demostración',
  'signin.loading': 'Cargando el inicio de sesión de demostración…',
  'signin.unreachable': 'No se pudo cargar el inicio de sesión de demostración.',
  'signin.unavailable': 'La demostración no está disponible en este momento.',
  'signin.intro': 'Esta es una demostración. Inicie sesión con uno de los siguientes perfiles.',
  'signin.personaLabel': 'Perfil',
  'signin.accessCodeLabel': 'Código de acceso',
  'signin.refused': 'No se aceptó el código de acceso o el perfil. Inténtelo de nuevo.',
  'signin.accessCodeHint': 'Ingrese el código de acceso para continuar.',
  'signin.submit': 'Iniciar sesión',
  'signin.submitting': 'Iniciando sesión…',
}
