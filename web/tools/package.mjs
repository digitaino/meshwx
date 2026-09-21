#!/usr/bin/env node
// Assemble the client as a folder somebody can download, unzip and run.
//
//     node tools/package.mjs [--out <dir>] [--zip] [--no-outlines]
//
// The client is already just files, so this copies rather than builds: there is no bundler, no
// minifier and no step that could change what runs. The one thing the repository does not hold in
// place is the preload bundle, which lives beside the bot in `meshcore_weather/client_data/` and is
// served at `/data/` by the dev server (`tools/dev-server.mjs`); here it is copied in as `data/`,
// so the result needs nothing but a web server.
//
// `--no-outlines` leaves out the two geojson files, 15 MB of zone and county polygons. The client
// treats a missing one as an empty table, so the maps then draw the basemap and the weather without
// the shapes. Worth it for a package meant to be downloaded over the air; not the default.
import { cp, mkdir, rm, readFile, writeFile, readdir, stat } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { MeshWXTables } from '../src/meshwx/index.js'

const run = promisify(execFile)
const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const bundleRoot = resolve(webRoot, '../meshcore_weather/client_data')

/** Everything the page itself asks for, in the shape it asks for it. */
const CLIENT = ['index.html', 'manifest.webmanifest', 'sw.js', 'src', 'styles', 'strings', 'assets', 'demo']
/** The bundle the client reads through `data/`, named by the codec so the two cannot drift. */
const TABLES = MeshWXTables.bundleFiles.map((name) => `${name}.json`)
const OUTLINES = ['zones.geojson', 'counties.geojson']
/** Copied in beside them: how to serve the folder, how to start it without a terminal, and what
 *  it is. `cp` keeps the mode, so `start-macos.command` stays executable and double-clickable. */
const EXTRAS = ['serve.mjs', 'start-macos.command', 'start-windows.bat', 'README.md']

export async function build({ out, outlines = true, log = () => {} } = {}) {
  const target = resolve(out)
  const missing = [...CLIENT, ...TABLES.map((f) => join('..', 'meshcore_weather', 'client_data', f))]
    .filter((f) => !existsSync(resolve(webRoot, f)))
  if (missing.length) throw new Error(`not in the repository: ${missing.join(', ')}`)

  await rm(target, { recursive: true, force: true })
  await mkdir(join(target, 'data'), { recursive: true })
  for (const item of CLIENT) await cp(join(webRoot, item), join(target, item), { recursive: true })
  for (const file of [...TABLES, ...(outlines ? OUTLINES : [])]) {
    if (!existsSync(join(bundleRoot, file))) {
      log(`  bundle file absent, left out: ${file}`)
      continue
    }
    await cp(join(bundleRoot, file), join(target, 'data', file))
  }
  for (const extra of EXTRAS) await cp(join(here, 'package', extra), join(target, extra))
  await stamp(target, { outlines })

  const files = await walk(target)
  const bytes = files.reduce((sum, f) => sum + f.size, 0)
  return { target, files: files.length, bytes, outlines }
}

/**
 * The first thing under the title says which build this is. A person who downloaded a zip in April
 * and is asking a question in June has no other way to tell, and neither has whoever answers.
 */
async function stamp(target, { outlines }) {
  const path = join(target, 'README.md')
  const readme = await readFile(path, 'utf8')
  const protocol = JSON.parse(await readFile(join(target, 'data', 'protocol.json'), 'utf8'))
  const stamp = [
    `*MeshWX ${new Date().toISOString().slice(0, 10)}`,
    `${await commit()}, protocol bundle ${protocol.version}`,
    outlines ? null : 'without the zone and county outlines',
  ].filter(Boolean).join(', ') + '.*'
  await writeFile(path, readme.replace(/^(# MeshWX\n)/, `$1\n${stamp}\n`))
}

async function commit() {
  try {
    const { stdout } = await run('git', ['-C', webRoot, 'rev-parse', '--short', 'HEAD'])
    return `build ${stdout.trim()}`
  } catch {
    return 'source not from a checkout'
  }
}

async function walk(dir) {
  const out = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...await walk(path))
    else out.push({ path, size: (await stat(path)).size })
  }
  return out
}

async function zip(target) {
  const name = `${target.split('/').pop()}.zip`
  try {
    await run('zip', ['-qr', name, target.split('/').pop()], { cwd: dirname(target) })
    return join(dirname(target), name)
  } catch (error) {
    throw new Error(`could not run zip (${error.code ?? error.message}); the folder is ready to archive by hand`)
  }
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2)
  const at = args.indexOf('--out')
  const out = at === -1 ? resolve(webRoot, '../dist/meshwx-web') : resolve(args[at + 1])
  const outlines = !args.includes('--no-outlines')
  const result = await build({ out, outlines, log: (m) => console.log(m) })
  console.log(`${relative(process.cwd(), result.target) || result.target}: ${result.files} files, ${(result.bytes / 1e6).toFixed(1)} MB`)
  if (args.includes('--zip')) console.log(`${relative(process.cwd(), await zip(result.target))}`)
  console.log(`\nTo check it: cd ${JSON.stringify(result.target)} && node serve.mjs`)
}
