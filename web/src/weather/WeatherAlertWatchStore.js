// Port of MC1Services/Services/Weather/WeatherAlertWatchStore.swift (docs/PORTING.md).

import { WeatherSavedPlacesStore } from './WeatherSavedPlacesStore.js'

/**
 * Where the evaluator reads what is watched, and where the screens write it.
 *
 * One interface for the three things that decide whether anything is posted at all — the saved
 * places with their bells, the two toggles, and the phone's own last position — so the tests can
 * hand the notifier a watch list without touching storage. Duck-typed:
 *
 *   watch() -> { places, myLocation, subscriptions }
 *
 * `defaults` is the synchronous injected key-value store described in
 * `WeatherSavedPlacesStore`; `places` is the screen layer's `WeatherSavedPlaces` rules.
 */
export class DefaultsWeatherAlertWatchStore {
  static key = 'weather.alertSubscriptions'

  constructor({ defaults, places }) {
    this.defaults = defaults
    this.savedPlaces = new WeatherSavedPlacesStore({ defaults, places })
    this.lastPosition = new WeatherLastPositionStore({ defaults })
  }

  /**
   * The production store: one key for the toggles, the saved places for the bells, and one for
   * the last position.
   *
   * The position is read only while My location is watched, and is cleared when that bell goes
   * off: a phone that is not watching its own position has no reason to keep one on disk.
   */
  watch() {
    const subscriptions = this.subscriptions
    return {
      places: this.savedPlaces.watched,
      myLocation: subscriptions.watchesMyLocation ? this.lastPosition.position : null,
      subscriptions
    }
  }

  get subscriptions() {
    return weatherAlertSubscriptions(this.defaults.get(DefaultsWeatherAlertWatchStore.key))
  }

  set subscriptions(newValue) {
    const value = weatherAlertSubscriptions(newValue)
    this.defaults.set(DefaultsWeatherAlertWatchStore.key, value)
    if (!value.watchesMyLocation) this.lastPosition.clear()
  }
}

/**
 * The two extras beyond storm warnings, and whether the phone's own position is watched
 * (docs/MESHWX_UI.md §16).
 *
 * Nothing here is on by default: a bell is the only thing that starts a subscription, and the
 * two toggles start off inside it. Every field absent is "nothing is watched", which is what a
 * phone that has never opened the screen means.
 */
export function weatherAlertSubscriptions(json) {
  return {
    watchesMyLocation: json?.watchesMyLocation ?? false,
    notifiesOtherWarnings: json?.notifiesOtherWarnings ?? false,
    notifiesTornadoNearby: json?.notifiesTornadoNearby ?? false
  }
}

/** Nothing is watched: the evaluator stops before it reads any state (docs/MESHWX_UI.md §3.1 N-2). */
export function isWatchEmpty(watch) {
  return watch.places.length === 0 && watch.myLocation == null
}

// MARK: - The phone's own position

/**
 * The last position the app knows, kept so a warning arriving after a relaunch still has
 * somewhere to be matched against (docs/MESHWX_UI.md §16).
 *
 * Written from whatever fix the app happens to take — it only has location while it is in use —
 * so this is "where the phone was", never a track: one row, overwritten, and only while My
 * location is watched. Turning that bell off deletes it.
 *
 * `{ latitude, longitude, horizontalAccuracy, timestamp }`; `horizontalAccuracy` is metres,
 * negative when the fix carried no usable accuracy, and `timestamp` is milliseconds.
 */
export class WeatherLastPositionStore {
  static key = 'weather.lastPosition'

  /**
   * How far apart two fixes have to be before the newer one is worth writing: a survey stream
   * delivers one a second, and a notification is matched within the place's own uncertainty
   * anyway. Seconds.
   */
  static minimumInterval = 60

  /** When this process last wrote a position, so a fix a second does not become a write a second. */
  static lastWrite = null

  constructor({ defaults }) {
    this.defaults = defaults
  }

  get position() {
    const stored = this.defaults.get(WeatherLastPositionStore.key)
    return stored == null ? null : stored
  }

  set position(newValue) {
    if (newValue == null) {
      this.defaults.set(WeatherLastPositionStore.key, null)
      return
    }
    this.defaults.set(WeatherLastPositionStore.key, newValue)
  }

  clear() {
    this.defaults.set(WeatherLastPositionStore.key, null)
    // The next fix after the bell goes back on is worth writing whenever it arrives.
    WeatherLastPositionStore.lastWrite = null
  }

  /**
   * Records a fix, if My location is watched and the last one written is a minute old.
   *
   * The whole rule lives here so the one call site in the location service is a single line
   * that cannot grow: a phone that is not watching its own position writes nothing at all.
   *
   * `places` is only needed to build the watch store that answers "is My location watched"; it
   * is the screen layer's `WeatherSavedPlaces` rules, as everywhere else in this file.
   */
  record({ latitude, longitude, horizontalAccuracy, timestamp, places }) {
    const store = new DefaultsWeatherAlertWatchStore({ defaults: this.defaults, places })
    if (!store.subscriptions.watchesMyLocation) return
    const last = WeatherLastPositionStore.lastWrite
    if (last != null && (timestamp - last) / 1000 < WeatherLastPositionStore.minimumInterval) return
    WeatherLastPositionStore.lastWrite = timestamp
    this.position = { latitude, longitude, horizontalAccuracy, timestamp }
  }
}

// MARK: - What has been posted

/**
 * Where the notifications this phone has posted are remembered, so a repeat of a warning
 * replaces one silently instead of sounding again — across a relaunch too. Duck-typed:
 *
 *   posts()      -> { [identifier]: WeatherAlertPost }
 *   save(posts)  -> void
 *
 * One JSON blob under one key, pruned by the screen layer's
 * `WeatherAlertNotificationRules.pruned`, so it can never become a history of the weather.
 */
export class DefaultsWeatherAlertPostLedger {
  static key = 'weather.alertPosts'

  constructor({ defaults }) {
    this.defaults = defaults
  }

  posts() {
    const stored = this.defaults.get(DefaultsWeatherAlertPostLedger.key)
    if (stored == null || typeof stored !== 'object' || Array.isArray(stored)) return {}
    return stored
  }

  save(posts) {
    this.defaults.set(DefaultsWeatherAlertPostLedger.key, posts)
  }
}
