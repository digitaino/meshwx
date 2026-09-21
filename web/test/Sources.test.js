// Every module under src/ is at least syntactically a module.
//
// The suites exercise the pure layers, and the browser-facing ones (the connection, the screens,
// the radio session) are reached only by a browser, so a broken import in one of them passed the
// whole suite and showed up as a blank page instead. This parses every file and evaluates none of
// them: `src/app/main.js` starts the app when it is loaded, and half the tree wants a `document`.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdir } from 'node:fs/promises'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const run = promisify(execFile)
// `.pathname` would keep the percent-encoding of a path with a space in it.
const root = fileURLToPath(new URL('../src/', import.meta.url))

async function* modules(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) yield* modules(path)
    else if (entry.name.endsWith('.js')) yield path
  }
}

// One child process for the lot: `vm.SourceTextModule` compiles a module and stops there, which
// is the whole question here, and it needs a flag this process was not started with.
const CHECKER = `
  const { readFileSync } = require('node:fs')
  const vm = require('node:vm')
  const broken = []
  for (const file of process.argv.slice(1)) {
    try { new vm.SourceTextModule(readFileSync(file, 'utf8'), { identifier: file }) }
    catch (error) { broken.push(file + ': ' + error.message) }
  }
  console.log(JSON.stringify(broken))
`

test('every module under src/ parses', async () => {
  const files = []
  for await (const file of modules(root)) files.push(file)
  assert.ok(files.length > 50, `only ${files.length} files found, so this is not checking much`)

  const { stdout } = await run(process.execPath, ['--experimental-vm-modules', '-e', CHECKER, '--', ...files])
  const broken = JSON.parse(stdout).map((line) => relative(root, line))
  assert.deepEqual(broken, [], `modules that do not parse:\n${broken.join('\n')}`)
})
