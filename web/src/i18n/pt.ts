// Brazilian Portuguese catalog, for a bank customer: "você" throughout, native verbs ("entrar",
// "acesso") instead of borrowed "login", and a first-person "Eu" for the customer's own messages.
// Every key in `Messages` must be present and non-blank, and every `{placeholder}` must survive;
// the catalog tests enforce both.
import type { Messages } from './messages'

/** Every interface message in Brazilian Portuguese. */
export const pt: Messages = {
  'app.title': 'Contestação de transações',
  'app.signOut': 'Sair',
  'app.sessionExpired': 'Sua sessão expirou. Entre novamente.',
  'common.retry': 'Tentar novamente',
  'common.error.generic': 'Ocorreu um erro.',
  'failure.offline': 'Sem conexão com o serviço. Verifique sua internet e tente novamente.',
  'failure.timeout': 'A resposta demorou demais. Tente novamente.',
  'failure.rateLimited': 'Muitas tentativas seguidas. Aguarde um minuto e tente novamente.',
  'failure.unavailable':
    'O serviço está indisponível no momento. Tente novamente em alguns minutos.',
  'chat.regionLabel': 'Conversa com o assistente',
  'chat.messagesLabel': 'Mensagens',
  'chat.messageLabel': 'Sua mensagem',
  'chat.starting': 'Iniciando a conversa…',
  'chat.couldNotStart': 'Não foi possível iniciar a conversa.',
  'chat.couldNotSend': 'Não foi possível enviar sua última mensagem.',
  'chat.noMessagesYet': 'Ainda não há mensagens.',
  'chat.assistantLabel': 'Assistente:',
  'chat.customerLabel': 'Eu:',
  'chat.confirm': 'Confirmar',
  'chat.send': 'Enviar',
  'chat.assistantTyping': 'O assistente está digitando…',
  'chat.notSent': 'Mensagem não enviada',
  'chat.charactersLeft': 'Caracteres restantes: {count}',
  'chat.ended': 'Esta conversa foi encerrada.',
  'chat.caseReference': 'Número do caso: {ticket}.',
  'signin.regionLabel': 'Acesso de demonstração',
  'signin.loading': 'Carregando o acesso de demonstração…',
  'signin.unreachable': 'Não foi possível carregar o acesso de demonstração.',
  'signin.unavailable': 'A demonstração não está disponível no momento.',
  'signin.intro': 'Esta é uma demonstração. Entre com um dos perfis abaixo.',
  'signin.personaLabel': 'Perfil',
  'signin.accessCodeLabel': 'Código de acesso',
  'signin.refused': 'O código de acesso ou o perfil não foi aceito. Tente novamente.',
  'signin.accessCodeHint': 'Informe o código de acesso para continuar.',
  'signin.submit': 'Entrar',
  'signin.submitting': 'Entrando…',
}
