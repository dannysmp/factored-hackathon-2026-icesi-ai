/** Parity test: every catalog must define every key with a non-blank value. `catalogParityProblems`
 * is exercised against deliberately broken fixtures, which the real catalog files cannot supply
 * because the type checker already keeps them key-complete, to prove it catches a missing key or a
 * blank value rather than only that the real catalogs happen to pass it. */
import { describe, expect, it } from 'vitest'
import { CATALOGS } from './catalogs'
import { catalogParityProblems } from './parity'

// `Messages`' keys are typed (not a plain index signature) so `useT` stays restricted to known
// keys; `catalogParityProblems` deliberately works on plain records instead, so this cast is the
// one place a catalog is read back as the untyped shape the checker itself operates on.
function asRecords(catalogs: typeof CATALOGS): Record<string, Record<string, string>> {
  return catalogs as unknown as Record<string, Record<string, string>>
}

/** Second-person forms that the formal register (usted) never uses, tuteo and voseo alike. Each is
 * matched as a whole word. */
const INFORMAL_WORDS = [
  'tú',
  'tu',
  'tus',
  'te',
  'tienes',
  'puedes',
  'quieres',
  'necesitas',
  'vos',
  'tenés',
  'podés',
  'querés',
  'necesitás',
]

/** Informal commands. One opens a clause (the start of the text, or after a sentence or clause
 * mark, a dash or an opening quote) — mid-clause the same spelling is a third-person verb or a
 * noun ("El asistente inicia…", "La espera fue larga"). A clause that opens with the noun
 * ("Espera estimada: 5 minutos") is flagged too and is better reworded. */
const INFORMAL_IMPERATIVES = [
  'escribe',
  'ingresa',
  'inicia',
  'intenta',
  'inténtalo',
  'reintenta',
  'revisa',
  'espera',
  'selecciona',
  'elige',
  'confirma',
  'vuelve',
  'usa',
  'acepta',
  'verifica',
  'corrige',
  'copia',
  'escribí',
  'ingresá',
  'iniciá',
  'intentá',
  'reintentá',
  'revisá',
  'esperá',
  'seleccioná',
  'elegí',
  'confirmá',
  'volvé',
  'usá',
  'aceptá',
  'verificá',
  'corregí',
  'copiá',
]

const WORD_PATTERN = new RegExp(`(?<!\\p{L})(?:${INFORMAL_WORDS.join('|')})(?!\\p{L})`, 'giu')
const IMPERATIVE_PATTERN = new RegExp(
  `(?<=^|[.!?¿¡,:;…—–«"“(]\\s*)(?:${INFORMAL_IMPERATIVES.join('|')})(?!\\p{L})`,
  'giu',
)

/** The informal-address findings in one text, lowercased. */
function informalSpanishAddress(text: string): string[] {
  return [...text.matchAll(WORD_PATTERN), ...text.matchAll(IMPERATIVE_PATTERN)].map((match) =>
    match[0].toLowerCase(),
  )
}

/** One sentence per entry of the two lists, each carrying only that entry, so dropping an entry
 * from a list fails here. */
const WORD_SAMPLES: Record<string, string> = {
  tú: 'Como tú ya sabes',
  tu: 'Revise tu código',
  tus: 'Revise tus datos',
  te: 'Gracias, te avisamos',
  tienes: 'Si tienes dudas',
  puedes: 'Si puedes esperar',
  quieres: 'Si quieres continuar',
  necesitas: 'Si necesitas ayuda',
  vos: 'Como vos ya sabés',
  tenés: 'Si tenés dudas',
  podés: 'Si podés esperar',
  querés: 'Si querés continuar',
  necesitás: 'Si necesitás ayuda',
}
const IMPERATIVE_SAMPLES: Record<string, string> = {
  escribe: 'Escribe su consulta',
  ingresa: 'Ingresa el código',
  inicia: 'Inicia sesión',
  intenta: 'Intenta de nuevo',
  inténtalo: 'Inténtalo de nuevo',
  reintenta: 'Reintenta en un minuto',
  revisa: 'Revisa el estado',
  espera: 'Espera un minuto',
  selecciona: 'Selecciona un perfil',
  elige: 'Elige un perfil',
  confirma: 'Confirma la acción',
  vuelve: 'Vuelve a empezar',
  usa: 'Usa el código entregado',
  acepta: 'Acepta los términos',
  verifica: 'Verifica el monto',
  corrige: 'Corrige el dato',
  copia: 'Copia el número',
  escribí: 'Escribí su consulta',
  ingresá: 'Ingresá el código',
  iniciá: 'Iniciá sesión',
  intentá: 'Intentá de nuevo',
  reintentá: 'Reintentá en un minuto',
  revisá: 'Revisá el estado',
  esperá: 'Esperá un minuto',
  seleccioná: 'Seleccioná un perfil',
  elegí: 'Elegí un perfil',
  confirmá: 'Confirmá la acción',
  volvé: 'Volvé a empezar',
  usá: 'Usá el código entregado',
  aceptá: 'Aceptá los términos',
  verificá: 'Verificá el monto',
  corregí: 'Corregí el dato',
  copiá: 'Copiá el número',
}

describe('the message catalogs', () => {
  it('define the same keys in all three languages, with no blank values', () => {
    expect(catalogParityProblems(asRecords(CATALOGS))).toEqual([])
  })

  it('keep every placeholder in every language, so a translation cannot drop it', () => {
    for (const messages of Object.values(CATALOGS)) {
      expect(messages['chat.charactersLeft']).toContain('{count}')
      expect(messages['chat.caseReference']).toContain('{ticket}')
    }
  })

  it('labels the customer\u2019s own messages in the first person, never as the system addressing them', () => {
    expect(CATALOGS.es['chat.customerLabel']).toBe('Yo:')
    expect(CATALOGS.pt['chat.customerLabel']).toBe('Eu:')
    expect(CATALOGS.en['chat.customerLabel']).toBe('You:')
  })

  it('uses each language\u2019s own words for signing in and for the demonstration identities', () => {
    const portuguese = Object.values(CATALOGS.pt).join(' ')
    const english = Object.values(CATALOGS.en).join(' ')

    expect(portuguese).not.toMatch(/\blogin\b/i)
    expect(english).not.toMatch(/\bpersonas?\b/i)
  })

  it('keeps Spanish in the formal register, with no informal address', () => {
    const spanish = asRecords(CATALOGS).es
    expect(Object.keys(spanish ?? {}).length).toBeGreaterThan(0)
    for (const text of Object.values(spanish ?? {})) {
      expect(informalSpanishAddress(text)).toEqual([])
    }
  })

  it('recognizes every informal word on its own', () => {
    expect(Object.keys(WORD_SAMPLES).sort()).toEqual([...INFORMAL_WORDS].sort())
    for (const [word, text] of Object.entries(WORD_SAMPLES)) {
      expect(informalSpanishAddress(text)).toEqual([word])
    }
  })

  it('recognizes every informal command opening a sentence', () => {
    expect(Object.keys(IMPERATIVE_SAMPLES).sort()).toEqual([...INFORMAL_IMPERATIVES].sort())
    for (const [command, text] of Object.entries(IMPERATIVE_SAMPLES)) {
      expect(informalSpanishAddress(text)).toEqual([command])
    }
  })

  it('recognizes an informal command at the start of any clause', () => {
    const informal = [
      'Por favor, espera un minuto',
      'Espera, un momento',
      'Perfil: elige uno',
      '«Espera» un minuto',
      'Espera… un minuto',
      'Gracias. Seleccioná un perfil',
      'Gracias — elegí uno',
      'Elegí… un perfil',
    ]

    for (const text of informal) expect(informalSpanishAddress(text)).not.toEqual([])
  })

  it('leaves the formal register alone, including words that are informal commands elsewhere', () => {
    const formal = [
      'La espera fue larga',
      'El asistente inicia la conversación',
      'Un agente revisa su caso',
      'El sistema intenta de nuevo',
      'Inicie sesión de nuevo.',
      'Espere un minuto e inténtelo de nuevo.',
      'Por favor, espere un minuto',
      'Perfil: elija uno',
      'Seleccione un perfil',
      'Si necesita ayuda, escriba',
      'Tiene que esperar',
      'Puede reintentar',
    ]

    for (const text of formal) expect(informalSpanishAddress(text)).toEqual([])
  })

  it('flags a catalog missing a key another catalog defines', () => {
    const broken = asRecords(CATALOGS)
    const pt = { ...broken.pt }
    delete pt['common.retry']

    expect(catalogParityProblems({ ...broken, pt })).not.toEqual([])
  })

  it('flags a catalog whose value is blank', () => {
    const broken = asRecords(CATALOGS)

    expect(
      catalogParityProblems({ ...broken, en: { ...broken.en, 'chat.send': '   ' } }),
    ).not.toEqual([])
  })
})
