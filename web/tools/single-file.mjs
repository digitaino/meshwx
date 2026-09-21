#!/usr/bin/env node
// Build the client as one HTML file that needs nothing installed.
//
//     node tools/single-file.mjs [--out <file>] [--no-outlines]
//
// A page opened straight from a folder (`file://`) may use Bluetooth and USB — Chrome counts it as
// a secure context — but it may not load a module or fetch a file beside it. So this build puts
// everything in the page: the modules bundled into one classic script, and every JSON file the
// client reads as `<script type="application/json" id="meshwx:<path>">`, which `readJSON`
// (`src/platform/files.js`) looks for before it tries the network.
//
// This is the one artifact that is not the repository's own files: esbuild flattens the imports.
// Nothing is minified, so the code in the page is still the code in `src/`, and everything else —
// the tables, the strings, the basemap — is embedded verbatim.
import { readFile, writeFile, readdir, stat } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join, resolve, relative } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import * as esbuild from 'esbuild'
import { MeshWXTables } from '../src/meshwx/index.js'

const run = promisify(execFile)
const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const bundleRoot = resolve(webRoot, '../meshcore_weather/client_data')

const OUTLINES = ['zones.geojson', 'counties.geojson']

/** Every file the client reads by name, as the path it asks for. */
async function shippedFiles({ outlines }) {
  const tables = MeshWXTables.bundleFiles.map((name) => [`data/${name}.json`, join(bundleRoot, `${name}.json`)])
  const geo = outlines ? OUTLINES.map((file) => [`data/${file}`, join(bundleRoot, file)]) : []
  const strings = (await readdir(join(webRoot, 'strings')))
    .filter((name) => name.endsWith('.json'))
    .map((name) => [`strings/${name}`, join(webRoot, 'strings', name)])
  return [
    ...tables, ...geo, ...strings,
    ['assets/basemap.json', join(webRoot, 'assets/basemap.json')],
    ['demo/datagrams.json', join(webRoot, 'demo/datagrams.json')],
  ]
}

/**
 * JSON inside a `<script>` element ends at the first `</script` in it, wherever that sits. `<` is
 * never structural in JSON, so escaping every one of them keeps the document out of the data's
 * way and leaves the data itself identical.
 */
const safeJSON = (text) => text.replaceAll('<', '\\u003c')

/**
 * A raw control character does not survive an HTML parser: inside a `<script>` a NUL becomes
 * U+FFFD, which reversed a character range in one of the client's regexes and threw the whole file
 * out before a line of it ran. They are perfectly legal in a `.js` file, so they are escaped here
 * rather than forbidden there. Tab, newline and carriage return are the code's own whitespace and
 * are left alone.
 */
const escapeControlCharacters = (js) =>
  js.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g,
    (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`)

async function commit() {
  try {
    const { stdout } = await run('git', ['-C', webRoot, 'rev-parse', '--short', 'HEAD'])
    return stdout.trim()
  } catch {
    return 'unknown'
  }
}

export async function build({ out, outlines = true } = {}) {
  const bundled = await esbuild.build({
    entryPoints: [join(webRoot, 'src/app/main.js')],
    bundle: true, format: 'iife', target: 'es2022', minify: false, write: false,
    legalComments: 'inline',
    // esbuild's default `charset: 'ascii'`, deliberately: the client has regexes with control
    // characters in them, and an HTML parser turns a raw NUL inside a <script> into U+FFFD, which
    // reverses a character range and throws the file out before a line of it runs.
    charset: 'ascii',
    // `src/l10n.js` reads the string tables off disk when it is running under Node, and that
    // branch is the only `import.meta` in the tree. A browser never reaches it: it is behind
    // `process.getBuiltinModule`, which no browser has.
    logOverride: { 'empty-import-meta': 'silent' },
  })
  const script = escapeControlCharacters(bundled.outputFiles[0].text)
  if (script.includes('</script')) throw new Error('the bundle contains </script and would end its own element')

  const html = await readFile(join(webRoot, 'index.html'), 'utf8')
  const styles = [...html.matchAll(/<link rel="stylesheet" href="([^"]+)">/g)].map((m) => m[1])
  let css = ''
  for (const sheet of styles) css += `/* ${sheet} */\n${await readFile(join(webRoot, sheet), 'utf8')}\n`

  const files = await shippedFiles({ outlines })
  let embedded = ''
  for (const [path, source] of files) {
    if (!existsSync(source)) continue
    embedded += `<script type="application/json" id="meshwx:${path}">${safeJSON(await readFile(source, 'utf8'))}</script>\n`
  }

  const protocol = JSON.parse(await readFile(join(bundleRoot, 'protocol.json'), 'utf8'))
  const stamp = `MeshWX ${new Date().toISOString().slice(0, 10)}, build ${await commit()}, `
    + `protocol bundle ${protocol.version}${outlines ? '' : ', without the zone and county outlines'}`

  const icon = await readFile(join(webRoot, 'assets/icon.svg'), 'utf8')
  // Every replacement below puts a file's contents into the page, so each one is a function:
  // as a string, `$&` and `$1` in the replacement are patterns, and the client's own
  // `escapeForRegExp` contains a `$&` that spliced the tag it was replacing into the bundle.
  const page = html
    .replace('<!doctype html>', () => `<!doctype html>\n<!-- ${stamp}. https://github.com/digitaino/meshwx -->`)
    .replace('<meta charset="utf-8">', () => `<meta charset="utf-8">\n  <meta name="generator" content="${stamp}">`)
    .replace(/\n\s*<link rel="manifest"[^>]*>/, '')              // there is no file beside this one
    .replace(/<link rel="icon" href="[^"]+" type="image\/svg\+xml">/,
      () => `<link rel="icon" href="data:image/svg+xml;base64,${Buffer.from(icon).toString('base64')}" type="image/svg+xml">`)
    .replace(/\n\s*<link rel="stylesheet" href="[^"]+">/g, '')
    .replace('</head>', () => `  <style>\n${css}  </style>\n</head>`)
    .replace(/\s*<script type="module" src="[^"]+"><\/script>/,
      () => `\n${embedded}<script>\n${script}</script>`)

  await writeFile(out, page)
  return { out, bytes: Buffer.byteLength(page), files: files.length, outlines }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2)
  const at = args.indexOf('--out')
  const out = at === -1 ? resolve(webRoot, '../dist/MeshWX.html') : resolve(args[at + 1])
  const result = await build({ out, outlines: !args.includes('--no-outlines') })
  console.log(`${relative(process.cwd(), result.out) || result.out}: ${(result.bytes / 1e6).toFixed(1)} MB, ${result.files} files embedded`)
}
