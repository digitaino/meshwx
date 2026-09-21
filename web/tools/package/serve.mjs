#!/usr/bin/env node
// MeshWX, served to this computer only.
//
//     node serve.mjs [--port 8137] [--open]
//
// Everything the client needs is in this folder, so this only hands out files: no internet, no
// database, nothing to configure. It listens on 127.0.0.1, which is where it has to be — a browser
// gives Bluetooth and USB only to a page from a "secure context", and `http://localhost` is one
// while `http://192.168.x.x` is not. To reach the client from a phone, put this folder behind
// https instead (README, "Put it on a web server").
import { createServer } from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { dirname, extname, join, normalize, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'

const root = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const port = Number(args[args.indexOf('--port') + 1]) || Number(process.env.PORT) || 8137
const openBrowser = args.includes('--open')

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.geojson': 'application/geo+json',
  '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.md': 'text/markdown; charset=utf-8',
}

// A URL can say `..`, and this folder sits in somebody's Downloads: resolve first, then check that
// the result is still inside.
function resolveInside(urlPath) {
  const path = normalize(join(root, decodeURIComponent(urlPath.split('?')[0])))
  if (path !== root && !path.startsWith(root + sep)) return null
  if (!existsSync(path)) return null
  return statSync(path).isDirectory() ? join(path, 'index.html') : path
}

createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { 'content-type': 'text/plain' })
    return res.end('only GET')
  }
  const file = resolveInside(req.url === '/' ? '/index.html' : req.url)
  if (!file || !existsSync(file)) {
    res.writeHead(404, { 'content-type': 'text/plain' })
    return res.end('not found')
  }
  res.writeHead(200, {
    'content-type': TYPES[extname(file)] ?? 'application/octet-stream',
    'content-length': statSync(file).size,
    'cache-control': 'no-store',
  })
  if (req.method === 'HEAD') return res.end()
  createReadStream(file).pipe(res)
}).listen(port, '127.0.0.1', () => {
  const url = `http://localhost:${port}`
  console.log(`MeshWX is at ${url}   (stop it with Control-C)`)
  if (!openBrowser) return
  const open = process.platform === 'darwin' ? ['open', [url]]
    : process.platform === 'win32' ? ['cmd', ['/c', 'start', '', url]] : ['xdg-open', [url]]
  spawn(open[0], open[1], { stdio: 'ignore', detached: true }).on('error', () => {}).unref()
}).on('error', (error) => {
  if (error.code !== 'EADDRINUSE') throw error
  console.error(`Port ${port} is busy. Try: node serve.mjs --port ${port + 1}`)
  process.exit(1)
})
