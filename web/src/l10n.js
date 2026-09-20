// User-facing text. The tables are the iOS app's Weather.strings, converted by
// tools/strings-to-json.mjs (docs/PORTING.md §6), plus web-only keys under `web.`.
//
// In the browser the app loads a table at boot and hands it to `setStrings`. Under Node (the
// tests) the English table is read from disk on first use, so a pure module can call `t`
// without any setup.

let table = null
let fallback = null
let strict = typeof process !== 'undefined' && !!process.versions?.node

/** Installs the active table, and optionally the English one to fall back to. */
export function setStrings(active, english = null) {
  table = active
  fallback = english
}

/** Whether an unknown key throws (tests) or returns the key (production). */
export function setStrict(value) { strict = value }

function nodeTable() {
  // Synchronous on purpose: `t` is called from pure, synchronous rules.
  const { readFileSync, readdirSync } = process.getBuiltinModule('node:fs')
  const { fileURLToPath } = process.getBuiltinModule('node:url')
  const directory = fileURLToPath(new URL('../strings/', import.meta.url))
  const read = (name) => {
    try { return JSON.parse(readFileSync(`${directory}${name}`, 'utf8')) }
    catch { return {} }
  }
  // Every `web.*.en.json`, not a named few: the web-only tables are split by screen and a new
  // one appears whenever a screen does. A test failing on a key that plainly exists, because
  // this list had not been added to, is not a failure anybody learns anything from.
  let web = {}
  try {
    for (const name of readdirSync(directory).sort()) {
      if (/^web\..*\.en\.json$/.test(name) || name === 'web.en.json') web = { ...web, ...read(name) }
    }
  } catch { /* no strings directory: `t` falls back to the key, or throws under test */ }
  // The iOS table wins: a web table only ever fills a gap it has (docs/PORTING.md §6).
  return { ...web, ...read('en.json') }
}

/**
 * The string for `key`, with `args` filling its format specifiers in order (`%@`, `%d`, `%lld`,
 * `%.1f`) or by position (`%1$@`). `%%` is a literal percent sign.
 */
export function t(key, ...args) {
  if (table == null && typeof process !== 'undefined' && process.getBuiltinModule) table = nodeTable()
  const template = table?.[key] ?? fallback?.[key]
  if (template == null) {
    if (strict) throw new Error(`l10n: no string for key "${key}"`)
    return key
  }
  return format(template, args)
}

/** True when the active or fallback table holds `key`. */
export function hasString(key) {
  if (table == null && typeof process !== 'undefined' && process.getBuiltinModule) table = nodeTable()
  return table?.[key] != null || fallback?.[key] != null
}

const SPECIFIER = /%(?:(\d+)\$)?(?:\.(\d+))?(@|d|i|u|ld|lld|lu|llu|f|s|%)/g

export function format(template, args) {
  let next = 0
  return template.replace(SPECIFIER, (whole, position, precision, kind) => {
    if (kind === '%') return '%'
    const value = args[position ? Number(position) - 1 : next++]
    if (value == null) return ''
    if (kind === 'f') return Number(value).toFixed(precision ? Number(precision) : 6)
    if (kind === '@' || kind === 's') return String(value)
    return String(Math.trunc(Number(value)))
  })
}
