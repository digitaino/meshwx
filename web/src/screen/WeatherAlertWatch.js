// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherAlertWatch.swift

import { WeatherAlertFolding } from './WeatherAlerts.js'
import { WeatherPlace, WeatherSavedPlace } from './WeatherPlace.js'

// MARK: - What the user asked to be told about

/**
 * The two extras beyond storm warnings, and whether the phone's own position is watched
 * (docs/MESHWX_UI.md §16): `{ watchesMyLocation, notifiesOtherWarnings, notifiesTornadoNearby }`.
 *
 * Nothing here is on by default: a bell is the only thing that starts a subscription, and the two
 * toggles start off inside it. Every field absent is "nothing is watched", which is what a phone
 * that has never opened the screen means — so `make` over a stored object with fields missing
 * reads as the Swift's tolerant decoder does.
 */
export const WeatherAlertSubscriptions = Object.freeze({
  make({ watchesMyLocation = false, notifiesOtherWarnings = false, notifiesTornadoNearby = false } = {}) {
    return { watchesMyLocation, notifiesOtherWarnings, notifiesTornadoNearby }
  },
})

/**
 * The phone's own position as the notifier reads it: the last fix the app happened to take, which
 * is only ever while the app was in use. `{ latitude, longitude, horizontalAccuracy, timestamp }`.
 *
 * Persisted on its own (`LocationService` records it, and only while My location is watched), so a
 * warning arriving after a relaunch is still matched against somewhere rather than nowhere. It is
 * never worded as following the user: every screen that shows it shows its age.
 */
export const WeatherLastPosition = Object.freeze({
  make({ latitude, longitude, horizontalAccuracy, timestamp }) {
    return { latitude, longitude, horizontalAccuracy, timestamp }
  },

  sample(position) {
    return {
      latitude: position.latitude,
      longitude: position.longitude,
      horizontalAccuracy: position.horizontalAccuracy,
      timestamp: position.timestamp,
    }
  },
})

/**
 * Everything the evaluator reads before it decides anything: which places are watched, the phone's
 * last position when it is one of them, and the two toggles.
 */
export const WeatherAlertWatch = Object.freeze({
  make({ places = [], myLocation = null, subscriptions = WeatherAlertSubscriptions.make() } = {}) {
    return { places, myLocation, subscriptions }
  },

  /**
   * Nothing is watched: the evaluator stops before it reads any state. Opt-in is enforced here,
   * not by a preference somewhere else (docs/MESHWX_UI.md §3.1 N-2).
   */
  isEmpty(watch) {
    return watch.places.length === 0 && watch.myLocation == null
  },
})

// MARK: - The places a warning is matched against

/** One watched place, resolved for a warning that has just arrived: `{ id, place, positionAt }`. */
export const WeatherWatchedPlace = Object.freeze({
  /** The id of the phone's own position, which no saved place can collide with. */
  myLocationID: 'myLocation',

  /** `make({ id, place, positionAt })`, or `make({ saved })` for a saved place. */
  make(options) {
    if (options.saved !== undefined) {
      return { id: WeatherSavedPlace.id(options.saved), place: WeatherSavedPlace.place(options.saved), positionAt: null }
    }
    return { id: options.id, place: options.place, positionAt: options.positionAt ?? null }
  },

  isMyLocation(watched) {
    return watched.id === WeatherWatchedPlace.myLocationID
  },

  /**
   * The phone's own place, from the last position the app knows. `label` is resolved by the
   * caller, which has the tables; the age is the position's own.
   */
  myLocation(position, { label, now }) {
    return {
      id: WeatherWatchedPlace.myLocationID,
      place: WeatherPlace.location(WeatherLastPosition.sample(position), { label, now }),
      positionAt: position.timestamp,
    }
  },
})

// MARK: - Gate

/**
 * Whether a warning reaches the user at all, and with what (docs/MESHWX_UI.md §16).
 *
 * The ranks are `WeatherAlertPriority.rank`: 0 Tornado, 1 Extreme Wind, 2 catastrophic Flash
 * Flood, 3 tornado-tagged Severe Thunderstorm, 4 Flash Flood, 5 Severe Thunderstorm, 6 other
 * warnings, 7–9 watches, advisories and statements.
 */
export const WeatherAlertDelivery = Object.freeze({
  /** Rank 0–5 covering the place, and a nearby tornado the user asked for: a sound. */
  sound: 'sound',
  /** Rank 6 with the toggle on: in Notification Center, no sound, no banner. */
  silent: 'silent',
})

export const WeatherAlertGate = Object.freeze({
  /** The highest rank that is a storm warning, and the only ranks a backlog may still notify for. */
  stormWarningRank: WeatherAlertFolding.neverFoldedRank,
  /** Rank 6: a warning that is not one of the six storm warnings. */
  otherWarningRank: 6,
  /** Tornado and Extreme Wind: the only two that notify from *near* a place. */
  tornadoRank: 1,

  /**
   * How a warning of `rank` reaches a watched place, or null for nothing at all.
   *
   * - Storm warnings (0–5) notify with sound, and only where they cover the place.
   * - Other warnings (6) are the opt-in silent toggle, again only where they cover.
   * - Watches, advisories and statements (7–9) never notify; they stay on the dashboard.
   * - A Tornado or Extreme Wind Warning within 50 km that does *not* cover the place is the second
   *   opt-in toggle. Near is only ever produced inside 50 km
   *   (`WeatherAlertPlacement.nearKilometres`).
   * - Anything the phone cannot place — no outline yet, no geometry at all, elsewhere — notifies
   *   nothing: "not placed" is never read as "here".
   */
  delivery({ rank, placement, subscriptions }) {
    switch (placement.kind) {
      case 'here':
        if (rank <= WeatherAlertGate.stormWarningRank) return WeatherAlertDelivery.sound
        if (rank === WeatherAlertGate.otherWarningRank) {
          return subscriptions.notifiesOtherWarnings ? WeatherAlertDelivery.silent : null
        }
        return null
      case 'near':
        if (rank > WeatherAlertGate.tornadoRank || !subscriptions.notifiesTornadoNearby) return null
        return WeatherAlertDelivery.sound
      default:
        return null
    }
  },
})
