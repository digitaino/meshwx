// Where this device is, for the My location page (docs/MESHWX_UI.md §5).
//
// The rule from the iOS tool holds here too: **nothing prompts for location until the user taps
// "Use my location"**. `start()` therefore only watches when permission is already granted;
// `request()` is what a tap calls, and it is the one call that may raise the browser's prompt.
//
// A fix is a `WeatherLocationSample`: `{ latitude, longitude, horizontalAccuracy (m), timestamp (ms) }`.
// The last one is kept in localStorage so a visit that starts without GPS (a desktop offline,
// a phone indoors) still has a last-known place, shown with its age.

const STORAGE_KEY = 'meshwx.location.last'

export class LocationService {
  constructor({ geolocation = globalThis.navigator?.geolocation, permissions = globalThis.navigator?.permissions, storage = safeStorage() } = {}) {
    this.geolocation = geolocation ?? null
    this.permissions = permissions ?? null
    this.storage = storage
    /** 'notDetermined' | 'authorized' | 'denied' | 'unsupported' */
    this.authorization = this.geolocation ? 'notDetermined' : 'unsupported'
    this.latestSample = this.#restore()
    this.listeners = new Set()
    this.watchID = null
  }

  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  /** Learns the permission state without prompting, and watches only when already granted. */
  async start() {
    if (!this.geolocation) return
    try {
      const status = await this.permissions?.query({ name: 'geolocation' })
      if (status) {
        this.#setAuthorization(mapPermission(status.state))
        status.onchange = () => {
          this.#setAuthorization(mapPermission(status.state))
          if (this.authorization === 'authorized') this.#watch()
          else this.#unwatch()
        }
      }
    } catch { /* Permissions API missing: stay notDetermined until the user asks. */ }
    if (this.authorization === 'authorized') this.#watch()
  }

  /** From a tap only: may raise the browser's permission prompt. Resolves to the fix or null. */
  request({ timeoutMs = 15000 } = {}) {
    if (!this.geolocation) return Promise.resolve(null)
    return new Promise((resolve) => {
      this.geolocation.getCurrentPosition(
        (position) => {
          this.#setAuthorization('authorized')
          const sample = this.#accept(position)
          this.#watch()
          resolve(sample)
        },
        (error) => {
          if (error.code === error.PERMISSION_DENIED) this.#setAuthorization('denied')
          this.#emit()
          resolve(null)
        },
        { enableHighAccuracy: false, maximumAge: 60_000, timeout: timeoutMs })
    })
  }

  stop() { this.#unwatch() }

  #watch() {
    if (this.watchID != null || !this.geolocation) return
    this.watchID = this.geolocation.watchPosition(
      (position) => this.#accept(position),
      (error) => { if (error.code === error.PERMISSION_DENIED) { this.#setAuthorization('denied'); this.#unwatch() } },
      { enableHighAccuracy: false, maximumAge: 60_000, timeout: 60_000 })
  }

  #unwatch() {
    if (this.watchID == null) return
    this.geolocation.clearWatch(this.watchID)
    this.watchID = null
  }

  #accept(position) {
    const { latitude, longitude, accuracy } = position.coords
    const sample = {
      latitude, longitude,
      horizontalAccuracy: Number.isFinite(accuracy) ? accuracy : -1,
      timestamp: position.timestamp || Date.now(),
    }
    this.latestSample = sample
    try { this.storage?.setItem(STORAGE_KEY, JSON.stringify(sample)) } catch { /* private mode */ }
    this.#emit()
    return sample
  }

  #restore() {
    try {
      const sample = JSON.parse(this.storage?.getItem(STORAGE_KEY) ?? 'null')
      return sample && Number.isFinite(sample.latitude) && Number.isFinite(sample.longitude) ? sample : null
    } catch { return null }
  }

  #setAuthorization(value) {
    if (this.authorization === value) return
    this.authorization = value
    this.#emit()
  }

  #emit() { for (const fn of this.listeners) fn(this) }
}

function mapPermission(state) {
  return state === 'granted' ? 'authorized' : state === 'denied' ? 'denied' : 'notDetermined'
}

function safeStorage() {
  try { return globalThis.localStorage ?? null } catch { return null }
}
