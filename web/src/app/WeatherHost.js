// The one interface the Weather tool has to the app around it.
//
// The Swift model reads `AppState` — the connection, the contacts, the channels, the services,
// location, the scene phase and the notification permission. The web app has no `AppState`;
// `RadioConnection` plays that part. Everything the model needs from the app is behind this one
// documented shape, so the model can be driven by `FakeWeatherHost` in a test and by the real
// connection in the browser without either knowing about the other.
//
// Nothing here touches a browser API at module level: `FakeWeatherHost` is plain objects, and the
// real host's browser work happens inside its own methods (docs/PORTING.md; the tool's rule 3).

import { WeatherSavedPlaces } from '../screen/index.js'

/**
 * @typedef {object} WeatherHost
 *
 * Required:
 * @property {(fn: (host: WeatherHost) => void) => () => void} subscribe
 *   Fires on **any** change below. The model diffs what it cares about, so a host may fire
 *   liberally: rebuilds are coalesced.
 * @property {object} weatherService
 *   The `WeatherService`. On the web it exists from boot, connected or not, which is why the
 *   Swift's "no services" path — reading the state file directly — has no counterpart here.
 * @property {boolean} isRadioConnected
 * @property {number|null} radioSessionStartedAt
 *   When this connection began, in milliseconds; null while nothing is connected. What "heard
 *   this radio session" is measured from.
 * @property {boolean|null} firmwareSupportsWeather
 *   Null when no radio has ever been seen, so no firmware claim can be made.
 * @property {string|null} firmwareVersion
 * @property {Array<{publicKey: Uint8Array, name: string, latitude: number, longitude: number,
 *   lastAdvertTimestamp: number, type?: number}>} contacts
 *   The radio's contact list. `lastAdvertTimestamp` is **whole seconds**, 0 for never — the
 *   shape `WeatherBot.fromContact` reads (docs/PORTING.md). Replace the array on every change;
 *   the model treats a new array identity as "the contacts changed".
 * @property {Array<{index: number, name: string, secret: Uint8Array}>} channels
 * @property {boolean} isChannelSyncDone
 *   The connect-time channel sync has finished. Until then the table may be empty and
 *   "#meshwx isn't set up" would be a guess.
 * @property {number} maxChannels
 * @property {() => Promise<void>} addWeatherChannel
 *   Writes `#meshwx` to the first free slot, from a tap only. It does what the Swift model does
 *   inline — read the channels back off the radio, refuse a slot the radio says is taken, write
 *   the channel — because all of that is the radio layer's business. It throws an `Error` whose
 *   `message` is already user-facing (`weather.channel.full`, `weather.channel.slotInUse`), and
 *   the model shows it as `errorMessage`.
 * @property {object} location
 *   `src/platform/location.js`'s `LocationService`: `authorization`, `latestSample`,
 *   `subscribe(fn)`, `start()`, `request()`.
 * @property {object} notifications
 *   `src/platform/notifications.js`: `authorization`, `request()`, `post()`, `remove(id)`.
 * @property {object} kv
 *   `src/platform/kv.js`'s `KeyValueStore`. The stores the model owns are synchronous
 *   (`UserDefaults` is), so the model wraps this in a `WeatherDefaults` cache it hydrates once.
 * @property {boolean} isVisible
 *   `document.visibilityState === 'visible'`: the web's scene phase. The tick stops while the
 *   page is hidden and a rebuild runs as soon as it is back.
 *
 * Optional:
 * @property {object} [defaults]
 *   A ready-made synchronous store, when the app already has one. Left out, the model builds a
 *   `WeatherDefaults` over `kv`.
 */

/**
 * A synchronous key-value store over the async `KeyValueStore`.
 *
 * `WeatherSavedPlacesStore`, `DefaultsWeatherAlertWatchStore`, `WeatherLastPositionStore` and
 * `WeatherRequestLogStore` are all ports of `UserDefaults`-backed Swift types, and the alert
 * evaluator reads the watch list in the middle of applying a message — so they are synchronous
 * and cannot become async without changing rules in a layer this one may not touch. The whole of
 * what they hold is five small values, so it is read once at boot and written through.
 *
 * A write lands in memory at once and reaches IndexedDB when it gets there; a failed write leaves
 * the visit working and nothing kept, which is what a private window does anyway.
 */
export class WeatherDefaults {
  /** Every key the tool keeps on the device. */
  static keys = Object.freeze([
    'weather.savedPlaces',
    'weather.alertSubscriptions',
    'weather.lastPosition',
    'weather.alertPosts',
    'weather.requestLog',
    'weather.preferredBotID',
    /** Which areas the alert map asks for (spec revision 10, docs/MESHWX_UI.md §17). */
    'weather.areaSelection',
  ])

  constructor({ kv = null, keys = WeatherDefaults.keys, values = {} } = {}) {
    this.kv = kv
    this.keys = keys
    this.values = new Map(Object.entries(values))
    this.isHydrated = kv == null
    this.writes = Promise.resolve()
  }

  /** Reads every key into memory. Safe to call again; only the first call reads. */
  async hydrate() {
    if (this.isHydrated || this.kv == null) return this
    this.isHydrated = true
    await Promise.all(
      this.keys.map(async (key) => {
        try {
          const value = await this.kv.get(key)
          if (value != null && !this.values.has(key)) this.values.set(key, value)
        } catch {
          /* Storage blocked: the visit works and nothing is kept. */
        }
      }),
    )
    return this
  }

  get(key) {
    const value = this.values.get(key)
    return value === undefined ? null : value
  }

  set(key, value) {
    if (value == null) {
      this.values.delete(key)
    } else {
      this.values.set(key, value)
    }
    if (this.kv == null) return
    this.writes = this.writes.then(
      () => (value == null ? this.kv.delete(key) : this.kv.set(key, value)),
      () => undefined,
    ).catch(() => undefined)
  }

  /** Waits for the writes in flight; for a test and for a page that is closing. */
  flush() {
    return this.writes
  }
}

/**
 * A host a test drives by hand, and the reference implementation of the shape above.
 *
 * Every mutator ends in `notify()`, which is the contract the model relies on: assigning
 * `host.contacts` directly and not notifying is the one way to make the model look broken.
 */
export class FakeWeatherHost {
  constructor({
    weatherService = null,
    isRadioConnected = false,
    radioSessionStartedAt = null,
    firmwareSupportsWeather = true,
    firmwareVersion = 'v1.15.0',
    contacts = [],
    channels = [],
    isChannelSyncDone = true,
    maxChannels = 8,
    location = new FakeLocationService(),
    notifications = new FakeNotifications(),
    kv = null,
    defaults = new WeatherDefaults(),
    isVisible = true,
  } = {}) {
    this.weatherService = weatherService
    this.isRadioConnected = isRadioConnected
    this.radioSessionStartedAt = radioSessionStartedAt
    this.firmwareSupportsWeather = firmwareSupportsWeather
    this.firmwareVersion = firmwareVersion
    this.contacts = contacts
    this.channels = channels
    this.isChannelSyncDone = isChannelSyncDone
    this.maxChannels = maxChannels
    this.location = location
    this.notifications = notifications
    this.kv = kv
    this.defaults = defaults
    this.isVisible = isVisible
    /** What `addWeatherChannel` did, and what it should throw. */
    this.addedChannels = 0
    this.channelError = null
    this.listeners = new Set()
  }

  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  notify() {
    for (const fn of [...this.listeners]) fn(this)
  }

  /** Sets fields and fires one change, the way a real host does. */
  change(fields) {
    Object.assign(this, fields)
    this.notify()
  }

  async addWeatherChannel() {
    if (this.channelError != null) throw new Error(this.channelError)
    this.addedChannels += 1
    this.notify()
  }
}

/** The `LocationService` shape, without a browser. */
export class FakeLocationService {
  constructor({ authorization = 'notDetermined', latestSample = null } = {}) {
    this.authorization = authorization
    this.latestSample = latestSample
    this.listeners = new Set()
    this.startCount = 0
    this.requestCount = 0
    /** What the next `request()` resolves to; null is a refusal or a timeout. */
    this.nextFix = null
  }

  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  async start() {
    this.startCount += 1
  }

  async request() {
    this.requestCount += 1
    if (this.nextFix == null) return null
    this.latestSample = this.nextFix
    this.authorization = 'authorized'
    this.emit()
    return this.nextFix
  }

  stop() {}

  /** A fix arriving from the watch. */
  deliver(sample) {
    this.latestSample = sample
    this.emit()
  }

  emit() {
    for (const fn of [...this.listeners]) fn(this)
  }
}

/** The notification poster's shape, without a browser. */
export class FakeNotifications {
  constructor({ authorization = 'notDetermined' } = {}) {
    this.authorization = authorization
    this.posted = []
    this.removed = []
    /** What `request()` grants. */
    this.grants = true
  }

  async request() {
    this.authorization = this.grants ? 'authorized' : 'denied'
    return this.authorization === 'authorized'
  }

  async isAuthorized() {
    return this.authorization === 'authorized'
  }

  async post(notification) {
    this.posted.push(notification)
  }

  async remove({ identifiers }) {
    this.removed.push(...identifiers)
  }

  async deliveredIdentifiers() {
    return this.posted.map((one) => one.identifier)
  }
}

/** The saved-place rules the stores take, in one place so every caller hands over the same one. */
export const WeatherHostPlaceRules = WeatherSavedPlaces
