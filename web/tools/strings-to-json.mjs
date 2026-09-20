// Converts the iOS app's Weather.strings tables into web/strings/<locale>.json.
//
//   node tools/strings-to-json.mjs [path/to/DigitainoMesh]
//
// The iOS table is the one source of user-facing text (docs/PORTING.md §6), so the web client
// says the same words as the phone in all of its locales. Re-run after the app's strings change.
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const appRepo = resolve(process.argv[2] ?? join(here, '../../../DigitainoMesh'))
const localization = join(appRepo, 'MC1/Resources/Localization')
const out = join(here, '../strings')

/** Parses an Apple .strings file: `"key" = "value";` with C escapes and comments. */
export function parseStrings(text) {
  const table = {}
  const pair = /"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;/g
  const stripped = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  for (const m of stripped.matchAll(pair)) table[unescape(m[1])] = unescape(m[2])
  return table
}

function unescape(s) {
  return s.replace(/\\(U[0-9a-fA-F]{4}|u[0-9a-fA-F]{4}|.)/g, (_, c) => {
    if (c[0] === 'U' || c[0] === 'u') return String.fromCharCode(parseInt(c.slice(1), 16))
    return { n: '\n', t: '\t', r: '\r', '"': '"', '\\': '\\', "'": "'" }[c] ?? c
  })
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (!existsSync(localization)) {
    console.error(`No iOS app at ${appRepo}; pass its path as the first argument.`)
    process.exit(1)
  }
  mkdirSync(out, { recursive: true })
  const locales = readdirSync(localization).filter((d) => d.endsWith('.lproj'))
  let english = null
  for (const dir of locales) {
    const file = join(localization, dir, 'Weather.strings')
    if (!existsSync(file)) continue
    const locale = dir.replace('.lproj', '')
    const table = parseStrings(readFileSync(file, 'utf8'))
    if (locale === 'en') english = table
    writeFileSync(join(out, `${locale}.json`), JSON.stringify(table, null, 1) + '\n')
    console.log(`${locale}: ${Object.keys(table).length} keys`)
  }
  if (!english) { console.error('en.lproj/Weather.strings not found'); process.exit(1) }
}
