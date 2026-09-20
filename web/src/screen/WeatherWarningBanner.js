// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherWarningBanner.swift

import { WeatherAlertItem } from './WeatherAlerts.js'

/**
 * The one strip above the weather when an alert covers the place (docs/MESHWX_UI.md §7):
 * `{ item, more }`.
 *
 * Always the same height, whatever is happening: one alert named, its end time, and a count of the
 * others. A tornado warning and a heat advisory are the same shape on the page — the colour and
 * the name carry the difference — because a card that grows with the weather is a card the eye
 * stops trusting, and because the page below it has to stay where it was.
 *
 * Only `WeatherAlertPlacement.here` earns the strip: an alert whose outline has not loaded, or one
 * that names no area at all, is never read as covering the place. Those are on the radio page, in
 * the alerts list, where they can be read rather than reacted to.
 *
 * The Swift memberwise initialiser is not exported: the Swift's own `make(_ items:)` takes that
 * name, and a banner is a plain `{ item, more }`.
 */
export const WeatherWarningBanner = Object.freeze({
  make(items) {
    const here = items.filter((item) => item.placement.kind === 'here')
    let first = null
    for (const item of here) {
      if (first == null || WeatherWarningBanner.isBefore(item, first)) first = item
    }
    if (first == null) return null
    return { item: first, more: here.length - 1 }
  },

  /**
   * §7.2's order, narrowed to the alerts that cover the place: a live alert before one that has
   * just expired, then the event's rank, then the soonest expiry.
   */
  isBefore(lhs, rhs) {
    const lhsLive = WeatherWarningBanner.isLive(lhs)
    const rhsLive = WeatherWarningBanner.isLive(rhs)
    if (lhsLive !== rhsLive) return lhsLive
    if (lhs.rank !== rhs.rank) return lhs.rank < rhs.rank
    const lhsExpires = WeatherAlertItem.expiresAt(lhs)
    const rhsExpires = WeatherAlertItem.expiresAt(rhs)
    if (lhsExpires !== rhsExpires) return lhsExpires < rhsExpires
    return lhs.identity.etn < rhs.identity.etn
  },

  /**
   * An upgrade whose replacement never arrived is live: it is the warning that is missing, not a
   * warning that ended.
   */
  isLive(item) {
    return item.kind.kind !== 'expiredRecently'
  },
})
