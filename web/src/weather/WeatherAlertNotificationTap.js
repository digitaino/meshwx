// Port of MC1Services/Services/Weather/WeatherAlertNotificationTap.swift (docs/PORTING.md).

import { WeatherAlertNotificationKeys } from './WeatherAlertNotification.js'

/**
 * Where a tapped alert notification is left for the Weather tool to pick up.
 *
 * The notification handler is per connection and the tool's model lives for one visit, so
 * neither can hold what a tap asked for: this is the one process-wide place both can see. The
 * app's router reads it, opens the Weather tool, and the tool opens that alert.
 *
 * Deviation: Swift defaults `tappedAt` and `now` to `Date()`. A pure module never reads the
 * clock (PORTING.md §3), so both are parameters here.
 */
export class WeatherAlertNotificationTap {
  /** How long a tap waits for the Weather tool to open before it is dropped. Seconds. */
  static freshness = 10 * 60

  static #shared = null

  static get shared() {
    if (WeatherAlertNotificationTap.#shared == null) {
      WeatherAlertNotificationTap.#shared = new WeatherAlertNotificationTap()
    }
    return WeatherAlertNotificationTap.#shared
  }

  constructor() {
    /** The alert a tap asked for, until something takes it. Null most of the time. */
    this.pending = null
    this.#listeners = new Set()
  }

  #listeners

  /**
   * Swift marks the type `@Observable`; the JS equivalent is a subscription (PORTING.md §3).
   * `fn` is called with the pending target, or null, whenever it changes.
   */
  subscribe(fn) {
    this.#listeners.add(fn)
    return () => this.#listeners.delete(fn)
  }

  /** Reads a tapped notification's `userInfo`; anything that is not one of ours is ignored. */
  receive({ userInfo, at }) {
    if (userInfo?.[WeatherAlertNotificationKeys.type] !== WeatherAlertNotificationKeys.weatherAlert) return
    const event = wholeNumber(userInfo[WeatherAlertNotificationKeys.event], 0xff)
    const office = wholeNumber(userInfo[WeatherAlertNotificationKeys.office], 0xff)
    const etn = wholeNumber(userInfo[WeatherAlertNotificationKeys.etn], 0xffff)
    const botID = wholeNumber(userInfo[WeatherAlertNotificationKeys.botID], 0xffff)
    if (event == null || office == null || etn == null || botID == null) return
    this.#set(WeatherAlertNotificationTapTarget.make({
      identity: { event, office, etn },
      botID,
      placeID: userInfo[WeatherAlertNotificationKeys.placeID] ?? '',
      tappedAt: at
    }))
  }

  /**
   * Takes the pending alert, leaving nothing behind: a tap opens one screen, once — and only
   * while it is recent, so a tap the user never followed does not open an alert an hour later.
   */
  take({ now }) {
    const target = this.pending
    this.#set(null)
    if (target == null) return null
    return (now - target.tappedAt) / 1000 < WeatherAlertNotificationTap.freshness ? target : null
  }

  #set(target) {
    this.pending = target
    for (const listener of this.#listeners) listener(target)
  }
}

/**
 * What the tapped notification named. `tappedAt` is when the tap happened, so a tap nobody
 * followed up on does not open an alert an hour later.
 */
export const WeatherAlertNotificationTapTarget = {
  make({ identity, botID, placeID, tappedAt }) {
    return { identity, botID, placeID, tappedAt }
  }
}

function wholeNumber(text, ceiling) {
  if (typeof text !== 'string' || !/^\d+$/.test(text)) return null
  const value = Number(text)
  return value <= ceiling ? value : null
}
