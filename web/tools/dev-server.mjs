// Development server: the static client, the preload bundle, and a proxy to the bot's debug bridge.
//
//   node tools/dev-server.mjs [--port 8137]
//
// - `/`            → web/ (no caching, so an edit shows on reload)
// - `/data/<file>` → ../meshcore_weather/client_data/<file>, the bundle the bot itself reads
// - `/api/bridge/` → the bot's debug bridge (docs: meshcore_weather/portal/routes/bridge.py),
//                    normally an SSH tunnel on 127.0.0.1:18081. The token is added here, read from
//                    the file named by MESHWX_BRIDGE_TOKEN_FILE (default: web/.bridge-token, which
//                    git ignores), so it never reaches the browser.
//
// Web Bluetooth and Web Serial need a secure context; http://localhost counts as one.
import { createServer, request as httpRequest } from 'node:http'
import { createReadStream, existsSync, statSync, readFileSync, appendFileSync } from 'node:fs'
import { dirname, extname, join, normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(here, '..')
const dataRoot = resolve(here, '../../meshcore_weather/client_data')
const args = process.argv.slice(2)
const port = Number(args[args.indexOf('--port') + 1]) || Number(process.env.PORT) || 8137
const bridge = new URL(process.env.MESHWX_BRIDGE_URL ?? 'http://127.0.0.1:18081')
// The token never lives in the repository: `.bridge-token` is an ignored file or a symlink to one.
const tokenFile = process.env.MESHWX_BRIDGE_TOKEN_FILE ?? join(webRoot, '.bridge-token')

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.geojson': 'application/geo+json',
  '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.md': 'text/markdown; charset=utf-8',
}

function bridgeToken() {
  if (!tokenFile || !existsSync(tokenFile)) return null
  return readFileSync(tokenFile, 'utf8').trim() || null
}

function safeJoin(root, urlPath) {
  const path = normalize(join(root, decodeURIComponent(urlPath)))
  return path === root || path.startsWith(root + sep) ? path : null
}

function serveFile(res, path) {
  if (!path || !existsSync(path)) return notFound(res)
  let file = path
  if (statSync(file).isDirectory()) file = join(file, 'index.html')
  if (!existsSync(file)) return notFound(res)
  res.writeHead(200, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
    'content-length': statSync(file).size,
    'cache-control': 'no-store',
  })
  createReadStream(file).pipe(res)
}

function notFound(res) {
  res.writeHead(404, { 'content-type': 'text/plain' })
  res.end('not found')
}

function proxyBridge(req, res) {
  const token = bridgeToken()
  if (!token) {
    res.writeHead(503, { 'content-type': 'application/json' })
    return res.end(JSON.stringify({ ok: false, detail: 'no bridge token: set MESHWX_BRIDGE_TOKEN_FILE' }))
  }
  const upstream = httpRequest({
    hostname: bridge.hostname, port: bridge.port || 80, method: req.method, path: req.url,
    headers: {
      accept: req.headers.accept ?? '*/*',
      'content-type': req.headers['content-type'] ?? 'application/json',
      'x-bridge-token': token,
      'x-bridge-client': req.headers['x-bridge-client'] ?? 'web',
      'x-requested-with': 'meshcore-portal',
    },
  }, (up) => {
    res.writeHead(up.statusCode ?? 502, {
      'content-type': up.headers['content-type'] ?? 'application/json',
      'cache-control': 'no-store', 'x-accel-buffering': 'no',
    })
    up.pipe(res)
  })
  upstream.on('error', (error) => {
    if (res.headersSent) return res.end()
    res.writeHead(502, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ok: false, detail: `bridge unreachable: ${error.code ?? error.message}` }))
  })
  res.on('close', () => upstream.destroy())
  req.pipe(upstream)
}

// `POST /__devlog`: the page's own account of its radio link, one JSON line each, appended to
// `web/.devlog.jsonl`. Bringing up real hardware happens in a browser tab nobody else can see;
// this is how whoever is helping reads what the tab saw. Localhost only, like everything here.
function devlog(req, res) {
  let body = ''
  req.on('data', (chunk) => { if (body.length < 20000) body += chunk })
  req.on('end', () => {
    try {
      const line = JSON.stringify({ at: new Date().toISOString(), ...JSON.parse(body) })
      appendFileSync(join(webRoot, '.devlog.jsonl'), line + '\n')
    } catch { /* a line that is not JSON is not worth keeping */ }
    res.writeHead(204).end()
  })
}

createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost')
  if (url.pathname.startsWith('/api/bridge/')) return proxyBridge(req, res)
  if (url.pathname === '/__devlog' && req.method === 'POST') return devlog(req, res)
  if (req.method !== 'GET' && req.method !== 'HEAD') return notFound(res)
  if (url.pathname.startsWith('/data/')) return serveFile(res, safeJoin(dataRoot, url.pathname.slice('/data/'.length)))
  return serveFile(res, safeJoin(webRoot, url.pathname))
}).listen(port, '127.0.0.1', () => {
  console.log(`MeshWX web on http://localhost:${port}  (bundle: ${dataRoot})`)
  console.log(bridgeToken() ? `bridge proxy → ${bridge.origin}` : 'bridge proxy off (no MESHWX_BRIDGE_TOKEN_FILE)')
})
