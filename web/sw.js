// Offline support. The tool is for places without internet, so after the first visit everything
// it needs must come from this cache: the code, the strings, the tables and, once a map has been
// opened, the 14 MB of outlines.
//
// Same-origin GETs are answered from the cache and refreshed in the background
// (stale-while-revalidate). `/api/` (the development bridge) is never cached.
const CACHE = 'meshwx-v1'
const SHELL = ['./', 'index.html', 'styles/app.css', 'manifest.webmanifest', 'assets/icon.svg', 'assets/basemap.json']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()))
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.includes('/api/')) return
  event.respondWith((async () => {
    const cache = await caches.open(CACHE)
    const cached = await cache.match(request, { ignoreSearch: url.pathname.endsWith('.html') || url.pathname.endsWith('/') })
    const refresh = fetch(request).then((response) => {
      if (response.ok) cache.put(request, response.clone())
      return response
    }).catch(() => null)
    if (cached) { event.waitUntil(refresh); return cached }
    return (await refresh) ?? new Response('offline', { status: 503, statusText: 'offline' })
  })())
})
