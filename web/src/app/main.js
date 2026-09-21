// Boot. Order: strings, the bundle's tables, storage, the weather service, the connection, the
// model, then the screens. Nothing here prompts for anything: location, Bluetooth, USB and
// notifications each wait for the tap that asks for them.
import { setStrings, setStrict, t } from '../l10n.js'
import { MeshWXTables, MeshWXGeometry } from '../meshwx/index.js'
import { WeatherService, KeyValueWeatherStateStore, WeatherTrafficLog } from '../weather/index.js'
import { KeyValueStore } from '../platform/kv.js'
import { LocationService } from '../platform/location.js'
import { Navigation } from '../ui/kit/nav.js'
import { h, clear } from '../ui/kit/dom.js'
import { RadioConnection } from './RadioConnection.js'
import { openConnectSheet, DefaultRadioPill } from '../ui/ConnectSheet.js'

const LOCALES = ['en', 'de', 'es', 'fr', 'it', 'nl', 'pl', 'pt', 'ru', 'uk', 'zh-Hans']
// The browser cannot list a directory, so the web-only tables are named here; `src/l10n.js`'s
// Node fallback globs them instead. Keep the two in step when a screen adds a table.
const WEB_TABLES = [
  'web.en.json', 'web.connect.en.json', 'web.place.en.json', 'web.radio.en.json',
]
const json = async (url) => {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url}: ${response.status}`)
  return response.json()
}
const optionalJSON = (url) => json(url).catch(() => ({}))
const bundleLoader = (name) => json(`data/${name}`)

function pickLocale() {
  const forced = new URLSearchParams(location.search).get('lang')
  for (const wanted of [forced, ...(navigator.languages ?? [navigator.language])].filter(Boolean)) {
    const exact = LOCALES.find((l) => l.toLowerCase() === wanted.toLowerCase())
    if (exact) return exact
    if (/^zh\b/i.test(wanted)) return 'zh-Hans'
    const base = LOCALES.find((l) => l === wanted.split('-')[0].toLowerCase())
    if (base) return base
  }
  return 'en'
}

async function loadStrings(locale) {
  const [english, ...web] = await Promise.all([
    json('strings/en.json'),
    ...WEB_TABLES.map((name) => optionalJSON(`strings/${name}`)),
  ])
  const webStrings = Object.assign({}, ...web)
  // The iOS table wins over the web-only ones, which only ever fill a gap in it (§6).
  const active = locale === 'en' ? english : await optionalJSON(`strings/${locale}.json`)
  setStrict(false)
  setStrings({ ...webStrings, ...active }, { ...webStrings, ...english })
}

async function boot() {
  const root = document.getElementById('app')
  const status = document.getElementById('boot-status')
  const locale = pickLocale()
  document.documentElement.lang = locale
  await loadStrings(locale)
  if (status) status.textContent = t('web.boot.tables')

  const tables = await MeshWXTables.load(bundleLoader)
  const geometry = MeshWXGeometry.configure(bundleLoader)

  const kv = new KeyValueStore()
  const locationService = new LocationService()
  // The traffic log goes into the same storage as the state, under its own key: it is a cache of
  // what went past on the channel, and it is read by the Channel traffic screen (§17).
  const weatherService = new WeatherService({
    transport: null,
    store: KeyValueWeatherStateStore.shared({ storage: kv }),
    trafficLog: new WeatherTrafficLog({ storage: kv }),
    tables,
  })
  const { createNotifications } = await import('../platform/notifications.js').catch(() => ({}))
  const notifications = createNotifications?.() ?? null
  const connection = new RadioConnection({ weatherService, kv, location: locationService, notifications, log: (m) => console.info('[meshwx]', m) })

  const { WeatherToolModel } = await import('./index.js')
  const model = new WeatherToolModel({ host: connection })

  clear(root)
  const nav = new Navigation(root, { backLabel: t('web.nav.back') })
  const app = {
    nav, model, connection, tables, geometry, location: locationService, kv, locale,
    basemap: null,
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    openConnect: () => openConnectSheet(app),
    radioPill: () => DefaultRadioPill({ app }),
  }
  globalThis.meshwx = app                                  // for the console, while this is young

  json('assets/basemap.json').then((basemap) => { app.basemap = basemap; nav.refresh() }).catch(() => {})
  import('../ui/radio/index.js').then((radio) => {
    if (radio.RadioPill) { app.radioPill = () => radio.RadioPill({ app }); nav.refresh() }
  }).catch(() => {})

  model.subscribe(() => nav.refresh())
  connection.subscribe(() => nav.refresh())

  const { WeatherToolScreen } = await import('../ui/place/index.js')
  nav.setRoot(WeatherToolScreen({ app }))

  await locationService.start()
  await model.start?.()
  await connection.start()

  openDeepLink(app).catch((error) => console.warn('[meshwx] deep link', error))

  const host = globalThis.location.hostname
  if ('serviceWorker' in navigator && host !== 'localhost' && host !== '127.0.0.1') {
    navigator.serviceWorker.register('sw.js').catch(() => {})
  }
}

/**
 * `?open=radio|alerts|map|radar|places|connect` opens that screen on arrival (home-screen
 * shortcuts, and looking at one screen from a script). It waits for the page it belongs to.
 */
async function openDeepLink(app) {
  const wanted = new URLSearchParams(globalThis.location.search).get('open')
  if (!wanted) return
  if (wanted === 'connect') return void app.openConnect()
  if (wanted === 'places') return void (await import('../ui/place/index.js')).openPlaces({ app })
  let page = null
  for (let i = 0; i < 40 && !page; i++) {
    page = app.model.screen?.({ for: app.model.selectedPageID }) ?? null
    if (!page) await new Promise((resolve) => setTimeout(resolve, 150))
  }
  if (!page) return
  // A first visit opens Places as a sheet. A screen pushed under an open sheet would be the one
  // its Done button pops, so the sheet goes first and its history entry is given time to unwind.
  if (app.nav.sheetStack.length) {
    for (const sheet of [...app.nav.sheetStack]) sheet.close()
    await new Promise((resolve) => setTimeout(resolve, 350))
  }
  const radio = await import('../ui/radio/index.js')
  const screen = {
    radio: radio.WeatherRadioScreen, alerts: radio.WeatherAlertsListScreen, map: radio.WeatherAreaMapScreen,
    fullmap: radio.WeatherAreaFullMapScreen, cached: radio.WeatherCachedScreen, traffic: radio.WeatherTrafficScreen,
    requests: radio.WeatherRequestsScreen, areas: radio.WeatherAreaPickerScreen,
    radar: radio.WeatherRadarScreen,
  }[wanted]
  if (screen) app.nav.push(screen({ app, page }))
}

boot().catch((error) => {
  console.error(error)
  const root = document.getElementById('app')
  clear(root)
  root.append(h('div', { class: 'boot' },
    h('p', null, safe('web.boot.failed', 'The weather tables could not be loaded: %@', error?.message ?? String(error))),
    h('button', { class: 'button', type: 'button', onclick: () => globalThis.location.reload() }, safe('web.boot.retry', 'Try again'))))
})

function safe(key, fallback, ...args) {
  try {
    const text = t(key, ...args)
    return text === key ? fallback.replace('%@', args[0] ?? '') : text
  } catch { return fallback.replace('%@', args[0] ?? '') }
}
