// The download (`tools/package.mjs`): a folder anybody can unzip and serve. What is checked here is
// completeness, because the way this breaks is quiet — a client that boots, then cannot find one
// table and shows a place with no ZIP codes or a map with no outlines.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { build } from '../tools/package.mjs'
import { MeshWXTables } from '../src/meshwx/index.js'

const out = await mkdtemp(join(tmpdir(), 'meshwx-package-'))
test.after(() => rm(out, { recursive: true, force: true }))

const full = join(out, 'full')
const result = await build({ out: full })

test('the folder is the client, the bundle beside it, and a way to serve it', () => {
  for (const file of ['index.html', 'manifest.webmanifest', 'sw.js', 'serve.mjs', 'README.md',
    'src/app/main.js', 'styles/app.css', 'strings/en.json', 'strings/web.en.json',
    'assets/basemap.json', 'assets/icon.svg', 'demo/datagrams.json']) {
    assert.ok(existsSync(join(full, file)), `missing ${file}`)
  }
  assert.ok(result.bytes > 10e6 && result.files > 100)
})

test('every table the codec names is in data/, under the name it asks for', () => {
  for (const name of MeshWXTables.bundleFiles) {
    assert.ok(existsSync(join(full, 'data', `${name}.json`)), `missing data/${name}.json`)
  }
  // Not in `bundleFiles`: the outlines, read lazily by MeshWXGeometry through the same loader.
  assert.ok(existsSync(join(full, 'data', 'zones.geojson')))
  assert.ok(existsSync(join(full, 'data', 'counties.geojson')))
})

test('nothing the client fetches by name is left out', async () => {
  const wanted = new Set()
  for await (const file of sources(join(full, 'src'))) {
    const text = await readFile(file, 'utf8')
    for (const [path] of text.matchAll(/\b(?:data|assets|demo)\/[\w./-]+\.(?:json|geojson|svg|png)\b/g)) {
      wanted.add(path)
    }
  }
  assert.ok(wanted.size > 0, 'the scan found nothing, so it is not checking anything')
  for (const path of wanted) assert.ok(existsSync(join(full, path)), `the client fetches ${path}, which is not in the package`)
})

test('the development scaffolding stays behind', async () => {
  const top = await readdir(full)
  for (const unwanted of ['test', 'tools', 'docs', 'package.json', '.bridge-token', '.devlog.jsonl', 'node_modules']) {
    assert.ok(!top.includes(unwanted), `${unwanted} does not belong in a download`)
  }
})

test('the README says which build it is', async () => {
  const readme = await readFile(join(full, 'README.md'), 'utf8')
  const stamp = readme.split('\n')[2]
  assert.match(stamp, /^\*MeshWX \d{4}-\d{2}-\d{2},.*protocol bundle 15\.\*$/)
  assert.doesNotMatch(readme, /without the zone and county outlines/)
})

test('--no-outlines drops the 15 MB of polygons and says so', async () => {
  const small = join(out, 'small')
  const thin = await build({ out: small, outlines: false })
  assert.ok(!existsSync(join(small, 'data', 'zones.geojson')))
  assert.ok(!existsSync(join(small, 'data', 'counties.geojson')))
  assert.ok(existsSync(join(small, 'data', 'zones.json')), 'the names and centroids are not optional')
  assert.ok(thin.bytes < result.bytes / 2)
  assert.match(await readFile(join(small, 'README.md'), 'utf8'), /without the zone and county outlines/)
})

async function* sources(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) yield* sources(path)
    else if (entry.name.endsWith('.js')) yield path
  }
}
