// Structural placeholders: real copy for the chat and sign-in screens migrates in separately,
// through the language review each catalog's strings go through before they ship.
import type { Messages } from './messages'

export const pt: Messages = {
  'common.loading': 'Carregando…',
  'common.retry': 'Tentar novamente',
  'common.error.generic': 'Ocorreu um erro.',
  'chat.placeholder': 'Escreva sua mensagem',
  'chat.messageLabel': 'Sua mensagem',
  'chat.starting': 'Iniciando a conversa…',
  'chat.couldNotStart': 'Não foi possível iniciar a conversa. Tente novamente.',
  'chat.couldNotSend': 'Não foi possível enviar sua última mensagem. Tente novamente.',
  'chat.noMessagesYet': 'Ainda não há mensagens.',
  'chat.assistantLabel': 'Assistente:',
  'chat.customerLabel': 'Você:',
  'chat.confirm': 'Confirmar',
  'chat.send': 'Enviar',
  'chat.ended': 'Esta conversa foi encerrada.',
  'chat.caseReference': 'Número do caso: {ticket}.',
  'signin.loading': 'Carregando o login de demonstração…',
  'signin.unreachable': 'Não foi possível conectar ao login de demonstração. Tente novamente.',
  'signin.unavailable': 'A demonstração não está disponível no momento.',
  'signin.intro': 'Esta é uma demonstração. Faça login com um dos perfis abaixo.',
  'signin.personaLabel': 'Perfil',
  'signin.accessCodeLabel': 'Código de acesso',
  'signin.refused': 'O código de acesso ou o perfil foram recusados. Tente novamente.',
  'signin.submit': 'Entrar',
}
