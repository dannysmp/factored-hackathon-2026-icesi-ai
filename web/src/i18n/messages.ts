/**
 * Every user-facing string key the catalogs must carry, one flat, namespaced key per string
 * (e.g. `chat.placeholder`). The type checker enforces that every catalog defines every key;
 * `catalogs.test.ts` additionally proves no catalog leaves a key with a blank value, something
 * the type checker can't express.
 */
export interface Messages {
  'common.loading': string
  'common.retry': string
  'common.error.generic': string
  'chat.placeholder': string
}
