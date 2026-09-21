// Drives the app in headless Chrome over the DevTools protocol and saves screenshots: the way to
// look at a screen from a script, with a phone-sized viewport, a fixed location and no chooser.
//
//   node tools/shoot.mjs <out-dir> <script.json>
//
// The script is a list of steps:
//   { "goto": "?link=demo" }                         load a URL (relative to http://localhost:8137/)
//   { "wait": 1500 }                                 milliseconds
//   { "waitFor": "css selector or text=Some text" }  up to 15 s
//   { "click": "css selector or text=Some text" }
//   { "type": "austin", "into": "css selector" }
//   { "eval": "js expression" }                      result is printed
//   { "shot": "name" }                               <out-dir>/<name>.png
//   { "text": true }                                 prints the visible text of the top screen
//   { "viewport": { "width": 390, "height": 844, "dark": true } }
//   { "geo": { "latitude": 30.2672, "longitude": -97.7431 } }   grants and fixes the location
import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const [outDir, scriptPath] = process.argv.slice(2)
if (!outDir || !scriptPath) { console.error('usage: node tools/shoot.mjs <out-dir> <script.json>'); process.exit(2) }
const steps = JSON.parse(readFileSync(scriptPath, 'utf8'))
const base = process.env.MESHWX_URL ?? 'http://localhost:8137/'
const chromePath = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const port = 9300 + Math.floor(Math.random() * 500)
mkdirSync(outDir, { recursive: true })

const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
  `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'meshwx-shoot-'))}`, 'about:blank',
], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch { /* not up yet */ }
    await sleep(200)
  }
  throw new Error('Chrome did not start')
}

const socket = new WebSocket(await target())
await new Promise((r) => socket.addEventListener('open', r, { once: true }))
let nextID = 0
const waiting = new Map()
const consoleLines = []
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data)
  if (message.id && waiting.has(message.id)) {
    const { resolve: ok, reject } = waiting.get(message.id)
    waiting.delete(message.id)
    message.error ? reject(new Error(message.error.message)) : ok(message.result)
  } else if (message.method === 'Runtime.exceptionThrown') {
    consoleLines.push(`EXCEPTION ${message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text}`)
  } else if (message.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(message.params.type)) {
    consoleLines.push(`${message.params.type.toUpperCase()} ${message.params.args.map((a) => a.value ?? a.description ?? '').join(' ')}`)
  }
})
const send = (method, params = {}) => new Promise((ok, reject) => {
  const id = ++nextID
  waiting.set(id, { resolve: ok, reject })
  socket.send(JSON.stringify({ id, method, params }))
})
const evaluate = async (expression) => {
  const { result, exceptionDetails } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text)
  return result.value
}

const FIND = `(sel) => {
  if (sel.startsWith('text=')) {
    const want = sel.slice(5)
    // The *last* open dialog: sheets stack (the connect sheet opens the radio settings over
    // itself), and the one on top is the only one a tap can reach.
    const scope = [...document.querySelectorAll('dialog[open]')].pop() ?? document
    const all = [...scope.querySelectorAll('button, a, [role=button], [role=menuitem], [role=tab], label, input')]
    const visible = all.filter((el) => el.getClientRects().length && !el.closest('[hidden]'))
    return visible.find((el) => (el.innerText || el.getAttribute('aria-label') || '').trim() === want)
      ?? visible.find((el) => (el.innerText || el.getAttribute('aria-label') || '').includes(want)) ?? null
  }
  return document.querySelector(sel)
}`

await send('Page.enable'); await send('Runtime.enable')
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] })

try {
  for (const step of steps) {
    if (step.viewport) {
      const { width = 390, height = 844, dark = false, mobile = width < 700 } = step.viewport
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 2, mobile })
      await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: dark ? 'dark' : 'light' }] })
    } else if (step.geo) {
      await send('Browser.grantPermissions', { permissions: ['geolocation'], origin: new URL(base).origin })
      await send('Emulation.setGeolocationOverride', { ...step.geo, accuracy: 30 })
    } else if (step.goto != null) {
      await send('Page.navigate', { url: new URL(step.goto, base).href })
      await sleep(step.settle ?? 2500)
    } else if (step.wait) await sleep(step.wait)
    else if (step.waitFor) {
      let found = false
      for (let i = 0; i < 75 && !found; i++) {
        found = await evaluate(`!!(${FIND})(${JSON.stringify(step.waitFor)})`)
        if (!found) await sleep(200)
      }
      if (!found) console.log(`! waitFor timed out: ${step.waitFor}`)
    } else if (step.click) {
      const ok = await evaluate(`(() => { const el = (${FIND})(${JSON.stringify(step.click)}); if (!el) return false; el.scrollIntoView({ block: 'center' }); el.click(); return true })()`)
      console.log(`${ok ? 'clicked' : '! not found'}: ${step.click}`)
      await sleep(step.settle ?? 700)
    } else if (step.type != null) {
      await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(step.into)}); el.focus(); el.value = ${JSON.stringify(step.type)}; el.dispatchEvent(new Event('input', { bubbles: true })) })()`)
      await sleep(step.settle ?? 700)
    } else if (step.eval) console.log('eval:', JSON.stringify(await evaluate(step.eval)))
    else if (step.text) {
      console.log(await evaluate(`(() => { const d = document.querySelector('dialog[open]'); const s = [...document.querySelectorAll('.screen')].filter((e) => !e.hidden).pop(); return ((d ?? s)?.innerText ?? document.body.innerText).slice(0, ${step.limit ?? 2500}) })()`))
    } else if (step.shot) {
      const { data } = await send('Page.captureScreenshot', { format: 'png' })
      const file = resolve(outDir, `${step.shot}.png`)
      writeFileSync(file, Buffer.from(data, 'base64'))
      console.log(`shot: ${file}`)
    }
  }
} finally {
  if (consoleLines.length) console.log(`--- console (${consoleLines.length})\n${[...new Set(consoleLines)].slice(0, 25).join('\n')}`)
  socket.close()
  chrome.kill()
}
