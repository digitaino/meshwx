// Port of MC1/Views/Tools/Weather/WeatherRequestLogStore.swift (docs/PORTING.md).

import { WeatherRequestLog } from '../screen/index.js'

/**
 * The requests this phone has put on the air, kept between visits (docs/MESHWX_UI.md §12).
 *
 * Device-local like the bot choice and the saved places: what this phone asked for is a fact
 * about this phone, and it belongs to no radio — the answers were broadcast to everyone, and only
 * the asking is this app's to remember. One JSON blob under one key, capped and aged by
 * `WeatherRequestLog`, so it can never become a history; an unreadable blob reads as an empty log
 * and the next request writes a good one.
 *
 * Two deviations from the Swift:
 *
 * - `defaults` is the injected **synchronous** key-value store `WeatherSavedPlacesStore` takes
 *   (`{ get(key), set(key, value) }`), not `UserDefaults`. Values go in as plain JSON, so there
 *   is no encoder.
 * - Swift reads `Date()` inside the accessors. `now` is injected here so a test can order a log
 *   without waiting.
 */
export class WeatherRequestLogStore {
  static key = 'weather.requestLog'

  constructor({ defaults, now = () => Date.now() }) {
    this.defaults = defaults
    this.now = now
  }

  get entries() {
    const stored = this.defaults.get(WeatherRequestLogStore.key)
    if (!Array.isArray(stored)) return []
    return WeatherRequestLog.ordered(stored, { now: this.now() })
  }

  set entries(newValue) {
    this.defaults.set(WeatherRequestLogStore.key, WeatherRequestLog.ordered(newValue, { now: this.now() }))
  }
}
