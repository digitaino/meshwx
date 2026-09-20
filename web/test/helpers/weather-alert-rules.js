// A stand-in for the screen layer's alert rules, for the `WeatherAlertNotifier` suite.
//
// `WeatherAlertPlacement`, `WeatherAlertPriority`, `WeatherAlertGate`,
// `WeatherAlertNotificationRules`, `WeatherWatchedPlace` and `WeatherPlace` all live under
// `Screen/` in the Swift, so in this port they belong to `src/screen/` — a layer `src/weather/`
// may not import (PORTING.md §9). The notifier therefore takes them as one injected `rules`
// object, and this file is the copy those tests run against: the same rules, ported straight
// from the Swift, with only what the notifier asks of them.
//
// When `src/screen/` lands, the app wires the real ones in and this file stays a test double.

import { MeshWXGeo } from '../../src/meshwx/index.js'
import { WeatherStateReducer, identityKey, identityString } from '../../src/weather/index.js'

// MARK: - Placement (Screen/WeatherAlerts.swift)

/** Within 50 km; the direction is from the place towards the alert. */
export const nearKilometres = 50
/** The radius a searched town spans (`WeatherPlace.searchedUncertaintyKilometres`). */
export const searchedUncertaintyKilometres = 5
const lastKnownAfter = 60 * 60
const freshFixAge = 5 * 60
const maximumDriftKilometres = 25

/** max(accuracy, 0.5 km) + 1 km for each minute of age past five, capped at 25 km. */
export function uncertainty({ accuracyMetres, age }) {
  const accuracy = accuracyMetres >= 0 ? accuracyMetres / 1000 : 1
  const drift = Math.min(Math.max(0, age - freshFixAge) / 60, maximumDriftKilometres)
  return Math.max(accuracy, 0.5) + drift
}

export const WeatherAlertPlacement = {
  here: Object.freeze({ kind: 'here' }),
  near({ kilometres, direction }) { return { kind: 'near', kilometres, direction } },
  checking: Object.freeze({ kind: 'checking' }),
  unplaced: Object.freeze({ kind: 'unplaced' }),
  elsewhere: Object.freeze({ kind: 'elsewhere' }),

  place(warning, { at: place, geometry, tables }) {
    const point = place.coordinate
    const radius = place.uncertaintyKilometres

    if (warning.polygon != null && warning.polygon.length >= 3) {
      // Storm-based products: the polygon is the warning; the county list is only what lies
      // under it.
      const distance = distanceToPolygon(point, warning.polygon)
      return classify({ distance, radius, from: point, towards: centreOf(warning.polygon) })
    }

    const areas = tables.namedAreas({ for: warning })
    if (areas.length === 0) return WeatherAlertPlacement.unplaced
    if (!geometry.isLoaded) return WeatherAlertPlacement.checking

    let nearest = null
    for (const area of areas) {
      const distance = geometry.distanceKilometres({ from: point, toArea: area.ugc })
      if (distance != null && (nearest == null || distance < nearest.distance)) {
        nearest = { distance, ugc: area.ugc }
      }
    }
    if (nearest == null) return WeatherAlertPlacement.unplaced
    if (nearest.distance <= radius) return WeatherAlertPlacement.here
    return classify({
      distance: nearest.distance, radius, from: point, towards: geometry.centre({ ofArea: nearest.ugc })
    })
  }
}

function classify({ distance, radius, from, towards }) {
  if (distance <= radius) return WeatherAlertPlacement.here
  if (distance <= nearKilometres) {
    return WeatherAlertPlacement.near({
      kilometres: distance,
      direction: towards == null ? 'north' : directionFrom(from, towards)
    })
  }
  return WeatherAlertPlacement.elsewhere
}

/** A decoded polygon is `[[latitude, longitude], …]`. */
function centreOf(polygon) {
  const latitude = polygon.reduce((sum, [lat]) => sum + lat, 0) / polygon.length
  const longitude = polygon.reduce((sum, [, lon]) => sum + lon, 0) / polygon.length
  return { latitude, longitude }
}

function distanceToPolygon(point, polygon) {
  if (containsPoint(point, polygon)) return 0
  let nearest = Infinity
  for (let index = 0; index < polygon.length; index += 1) {
    const [aLat, aLon] = polygon[index]
    const [bLat, bLon] = polygon[(index + 1) % polygon.length]
    nearest = Math.min(nearest, distanceToSegment(point, { aLat, aLon, bLat, bLon }))
  }
  return nearest
}

function containsPoint(point, polygon) {
  let inside = false
  for (let index = 0, previous = polygon.length - 1; index < polygon.length; previous = index, index += 1) {
    const [iLat, iLon] = polygon[index]
    const [jLat, jLon] = polygon[previous]
    const straddles = (iLat > point.latitude) !== (jLat > point.latitude)
    if (straddles
      && point.longitude < ((jLon - iLon) * (point.latitude - iLat)) / (jLat - iLat) + iLon) {
      inside = !inside
    }
  }
  return inside
}

/** Equirectangular projection around the point, which is exact enough inside a county. */
function distanceToSegment(point, { aLat, aLon, bLat, bLon }) {
  const scale = Math.cos((point.latitude * Math.PI) / 180)
  const px = 0
  const py = 0
  const ax = (aLon - point.longitude) * scale
  const ay = aLat - point.latitude
  const bx = (bLon - point.longitude) * scale
  const by = bLat - point.latitude
  const dx = bx - ax
  const dy = by - ay
  const lengthSquared = dx * dx + dy * dy
  const t = lengthSquared === 0
    ? 0
    : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSquared))
  const nearestLon = point.longitude + (ax + t * dx) / scale
  const nearestLat = point.latitude + (ay + t * dy)
  return MeshWXGeo.distanceKilometres({
    fromLat: point.latitude, fromLon: point.longitude, toLat: nearestLat, toLon: nearestLon
  })
}

const COMPASS = [
  'north', 'northNorthEast', 'northEast', 'eastNorthEast', 'east', 'eastSouthEast',
  'southEast', 'southSouthEast', 'south', 'southSouthWest', 'southWest', 'westSouthWest',
  'west', 'westNorthWest', 'northWest', 'northNorthWest'
]

function directionFrom(point, target) {
  const toRadians = Math.PI / 180
  const dLon = (target.longitude - point.longitude) * toRadians
  const y = Math.sin(dLon) * Math.cos(target.latitude * toRadians)
  const x = Math.cos(point.latitude * toRadians) * Math.sin(target.latitude * toRadians)
    - Math.sin(point.latitude * toRadians) * Math.cos(target.latitude * toRadians) * Math.cos(dLon)
  const bearing = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
  return COMPASS[Math.round(bearing / 22.5) % 16]
}

// MARK: - Priority (Screen/WeatherAlerts.swift)

export const WeatherAlertPriority = {
  /** Lower is more urgent. */
  rank(warning, { tables }) {
    return rankOf({
      vtec: tables.vtec({ for: warning.event }) ?? '',
      floodDamage: warning.flood_damage ?? 0,
      tornado: warning.tornado ?? 0
    })
  }
}

function rankOf({ vtec, floodDamage, tornado }) {
  if (vtec === 'TO.W') return 0
  if (vtec === 'EW.W') return 1
  if (vtec === 'FF.W' && floodDamage === 2) return 2
  if (vtec === 'SV.W' && tornado !== 0) return 3
  if (vtec === 'FF.W') return 4
  if (vtec === 'SV.W') return 5
  switch (vtec.split('.')[1]) {
    case 'W': return 6
    case 'A': return 7
    case 'Y': return 8
    case 'S': return 9
    default: return 10
  }
}

// MARK: - Gate (Screen/WeatherAlertWatch.swift)

export const WeatherAlertGate = {
  /** The highest rank that is a storm warning (`WeatherAlertFolding.neverFoldedRank`). */
  stormWarningRank: 5,
  /** Rank 6: a warning that is not one of the six storm warnings. */
  otherWarningRank: 6,
  /** Tornado and Extreme Wind: the only two that notify from *near* a place. */
  tornadoRank: 1,

  delivery({ rank, placement, subscriptions }) {
    switch (placement.kind) {
      case 'here':
        if (rank <= WeatherAlertGate.stormWarningRank) return 'sound'
        if (rank === WeatherAlertGate.otherWarningRank) {
          return subscriptions.notifiesOtherWarnings ? 'silent' : null
        }
        return null
      case 'near':
        if (rank > WeatherAlertGate.tornadoRank || !subscriptions.notifiesTornadoNearby) return null
        return 'sound'
      default:
        return null
    }
  }
}

// MARK: - Notification rules (Screen/WeatherAlertNotificationRules.swift)

export const WeatherAlertNotificationRules = {
  /** A repeat sounds again on an extension only once the last sounding post is this old. */
  escalationQuietPeriod: 20 * 60,
  /** An expiry is only "extended" past the minute the wire truncates to. */
  extensionMargin: WeatherStateReducer.digestMargin,
  /** A post is forgotten this long after the warning it stands for expired. */
  postRetention: 6 * 60 * 60,

  identifier({ botID, identity, placeID, tables }) {
    const name = identityString(identity, { tables })
      ?? `${identity.event}.${identity.office}.${identity.etn}`
    return `wx-${botID.toString(16).toUpperCase().padStart(4, '0')}-${name}@${placeID}`
  },

  threadIdentifier({ placeID }) {
    return `wx-place-${placeID}`
  },

  decide({ warning, rank, placement, delivery, isBacklog, posted, isDelivered, now }) {
    const expiresAt = warning.expires_min * 60_000
    // An expired warning never notifies, live or late.
    if (expiresAt <= now) return { kind: 'none' }
    // The phone cannot place this one: never read as "not here".
    if (placement.kind === 'checking' || placement.kind === 'unplaced') return { kind: 'none' }
    const coversPlace = placement.kind === 'here'
    // The gate closed on a warning already posted.
    if (delivery == null) return posted == null ? { kind: 'none' } : { kind: 'remove' }
    if (isBacklog && rank > WeatherAlertGate.stormWarningRank) return { kind: 'none' }

    if (posted == null) return { kind: 'post', sound: delivery === 'sound', isLate: isBacklog }
    if (isEscalation({ warning, coversPlace, posted, now })) {
      return { kind: 'post', sound: delivery === 'sound', isLate: isBacklog }
    }
    // A repeat or an update replaces what is on screen, silently. One the user has already
    // dismissed or opened is not brought back for that.
    if (!isDelivered) return { kind: 'none' }
    return { kind: 'post', sound: false, isLate: isBacklog }
  },

  record({ identifier, warning, placeID, botID, coversPlace, sound, posted, now }) {
    return {
      identifier,
      identity: { event: warning.event, office: warning.office, etn: warning.etn },
      placeID,
      botID,
      tornado: warning.tornado ?? 0,
      floodDamage: warning.flood_damage ?? 0,
      expiresMinutes: warning.expires_min,
      coversPlace,
      postedAt: now,
      alertedAt: sound ? now : (posted?.alertedAt ?? now),
      alertedExpiresMinutes: sound ? warning.expires_min : (posted?.alertedExpiresMinutes ?? warning.expires_min)
    }
  },

  pruned(posts, { now }) {
    return Object.fromEntries(
      Object.entries(posts).filter(
        ([, post]) => (now - post.expiresMinutes * 60_000) / 1000 < WeatherAlertNotificationRules.postRetention
      )
    )
  }
}

function isEscalation({ warning, coversPlace, posted, now }) {
  if ((warning.tornado ?? 0) > posted.tornado) return true
  if ((warning.flood_damage ?? 0) === 2 && posted.floodDamage !== 2) return true
  // Near became here: the reason the user asked to hear about tornadoes nearby was this moment.
  if (coversPlace && !posted.coversPlace) return true
  const extended = warning.expires_min * 60_000
    > posted.alertedExpiresMinutes * 60_000 + WeatherAlertNotificationRules.extensionMargin * 1000
  return extended && (now - posted.alertedAt) / 1000 > WeatherAlertNotificationRules.escalationQuietPeriod
}

// MARK: - Watched places (Screen/WeatherAlertWatch.swift, Screen/WeatherPlace.swift)

export const myLocationID = 'myLocation'

export const WeatherWatchedPlace = {
  make({ saved }) {
    return {
      id: savedPlaceID(saved),
      place: {
        coordinate: { latitude: saved.latitude, longitude: saved.longitude },
        label: saved.label,
        uncertaintyKilometres: searchedUncertaintyKilometres
      },
      positionAt: null
    }
  },

  myLocation(position, { label, now }) {
    const age = Math.max(0, (now - position.timestamp) / 1000)
    return {
      id: myLocationID,
      place: {
        coordinate: { latitude: position.latitude, longitude: position.longitude },
        label,
        uncertaintyKilometres: uncertainty({ accuracyMetres: position.horizontalAccuracy, age }),
        kind: age > lastKnownAfter ? 'lastKnown' : 'current'
      },
      positionAt: position.timestamp
    }
  }
}

/** `WeatherSavedPlace.id`: coordinates rounded to about 100 m. */
export function savedPlaceID(saved) {
  if (saved.zipCode != null) return `zip:${saved.zipCode}`
  if (saved.stationIndex != null) return `station:${saved.stationIndex}`
  return `at:${saved.latitude.toFixed(3)},${saved.longitude.toFixed(3)}`
}

/**
 * The saved-list rules `WeatherSavedPlacesStore` takes as `places`: enough of
 * `WeatherSavedPlaces` for the store's own tests. The screen layer's is the real one.
 */
export const WeatherSavedPlaces = {
  limit: 12,

  /** Newest choice first, watched places never pushed off the end, capped at `limit`. */
  ordered(list) {
    const sorted = [...list].sort((lhs, rhs) => rhs.chosenAt - lhs.chosenAt)
    if (sorted.length <= WeatherSavedPlaces.limit) return sorted
    const kept = []
    const spare = []
    for (const place of sorted) (place.isWatched ? kept : spare).push(place)
    const room = Math.max(0, WeatherSavedPlaces.limit - kept.length)
    const survivors = new Set([...kept, ...spare.slice(0, room)].map(savedPlaceID))
    return sorted.filter((place) => survivors.has(savedPlaceID(place)))
  },

  apply(edit, { to: list }) {
    switch (edit.kind) {
      case 'remember': {
        const existing = list.find((place) => savedPlaceID(place) === savedPlaceID(edit.value))
        const chosen = { ...edit.value, isWatched: existing?.isWatched ?? edit.value.isWatched }
        if (existing != null) {
          return WeatherSavedPlaces.ordered(
            list.map((place) => (savedPlaceID(place) === savedPlaceID(chosen) ? chosen : place))
          )
        }
        return WeatherSavedPlaces.ordered([chosen, ...list])
      }
      case 'remove':
        return list.filter((place) => savedPlaceID(place) !== edit.id)
      case 'watch':
        return list.map(
          (place) => (savedPlaceID(place) === edit.id ? { ...place, isWatched: edit.value } : place)
        )
      default:
        return list
    }
  },

  dropped(updated, { from: loaded }) {
    const after = new Set(updated.map(savedPlaceID))
    return loaded.map(savedPlaceID).filter((id) => !after.has(id))
  }
}

/** The whole object `WeatherAlertNotifier` is handed as `rules`. */
export function alertRules({ placeLabel = () => null } = {}) {
  return {
    placement: (warning, options) => WeatherAlertPlacement.place(warning, options),
    rank: (warning, options) => WeatherAlertPriority.rank(warning, options),
    delivery: (options) => WeatherAlertGate.delivery(options),
    identifier: (options) => WeatherAlertNotificationRules.identifier(options),
    threadIdentifier: (options) => WeatherAlertNotificationRules.threadIdentifier(options),
    decide: (options) => WeatherAlertNotificationRules.decide(options),
    record: (options) => WeatherAlertNotificationRules.record(options),
    pruned: (posts, options) => WeatherAlertNotificationRules.pruned(posts, options),
    watchedPlace: (options) => WeatherWatchedPlace.make(options),
    myLocationPlace: (position, options) => WeatherWatchedPlace.myLocation(position, options),
    placeLabel
  }
}

export { identityKey }
