// Port of MC1Services/Services/Weather/WeatherAlertNotification.swift (docs/PORTING.md).

import { t } from '../l10n.js'
import { MeshWXCompass } from '../meshwx/index.js'
import { dateFromUnixMinutes } from './WeatherBotState.js'

// MARK: - What is posted

/**
 * The words of one alert notification.
 *
 * - `title`: the event, "Tornado Warning".
 * - `subtitle`: where it came from, "National Weather Service alerts via WX-AUS".
 * - `body`: "Austin · until 9:41 PM · Tornado: radar indicated", and the late note under it when
 *   the message was drained from the radio's queue.
 */
export const WeatherAlertNotificationContent = {
  make({ title, subtitle, body }) { return { title, subtitle, body } }
}

/**
 * One notification, ready to post: what it says, how loudly, and everything a tap needs to open
 * the alert it stands for.
 *
 * `sound` false is a silent delivery: notification centre only, which is what a replacement and
 * the rank-6 toggle get.
 */
export const WeatherAlertNotification = {
  make({ identifier, threadIdentifier, content, sound, identity, botID, placeID }) {
    return { identifier, threadIdentifier, content, sound, identity, botID, placeID }
  },

  id(notification) { return notification.identifier },

  /**
   * The `userInfo` a tap is read back from (`WeatherAlertNotificationTap`). Strings only, so it
   * survives the round trip through the notification store.
   */
  userInfo(notification) {
    return {
      [WeatherAlertNotificationKeys.type]: WeatherAlertNotificationKeys.weatherAlert,
      [WeatherAlertNotificationKeys.event]: String(notification.identity.event),
      [WeatherAlertNotificationKeys.office]: String(notification.identity.office),
      [WeatherAlertNotificationKeys.etn]: String(notification.identity.etn),
      [WeatherAlertNotificationKeys.botID]: String(notification.botID),
      [WeatherAlertNotificationKeys.placeID]: notification.placeID
    }
  }
}

/** `userInfo` keys, shared by the poster and the tap reader. */
export const WeatherAlertNotificationKeys = Object.freeze({
  type: 'type',
  weatherAlert: 'weatherAlert',
  event: 'wxEvent',
  office: 'wxOffice',
  etn: 'wxEtn',
  botID: 'wxBot',
  placeID: 'wxPlace'
})

// MARK: - Copy

/**
 * One alert as the words are chosen for it.
 *
 * - `warning`: the decoded Warning message (PORTING.md §5).
 * - `placeLabel`: the watched place's short name, "Austin".
 * - `placement`: covering the place, or near it with a distance and a direction — the screen
 *   layer's `WeatherAlertPlacement`, `{ kind: 'here' | 'near' | 'elsewhere' | 'checking' |
 *   'unplaced', kilometres?, direction? }`.
 * - `botName`: the bot's advertised name where the phone knows it, "WX-AUS".
 * - `isLate`: the message was drained from the radio's queue at connect, so the warning was sent
 *   while the radio was out of range.
 */
export const WeatherAlertNotificationSubject = {
  make({ warning, placeLabel, placement, botName = null, isLate = false, now }) {
    return { warning, placeLabel, placement, botName, isLate, now }
  },

  expiresAt(subject) { return dateFromUnixMinutes(subject.warning.expires_min) }
}

/**
 * Where a notification's words come from. The interface, duck-typed:
 *
 *   content(subject, { tables }) -> WeatherAlertNotificationContent
 *   myLocationLabel              -> string
 *
 * `WeatherAlertDefaultCopy` below is the implementation over `web/strings`; an app that words
 * notifications differently installs its own through `WeatherAlertNotificationCopyRegistry`.
 */

/**
 * The words of an alert notification, from the shared string tables.
 *
 * Deviation: Swift ships two copies of this wording — an ad-hoc English fallback inside the
 * service package (`WeatherAlertDefaultCopy`) and the app target's localized
 * `WeatherAlertNotificationCopyImpl` over `Weather.strings`. The two do not agree word for word
 * (the fallback says "radar indicated" where the app says "Tornado: radar indicated"). The web
 * has one string table for every layer, so there is one copy, and it is the app's wording:
 * PORTING.md §6 forbids typing English into a module, and the table is what a phone actually
 * shows.
 */
export const WeatherAlertDefaultCopy = {
  get myLocationLabel() { return t('weather.notifications.myLocationLabel') },

  content(subject, { tables }) {
    const parts = []
    const place = shortName(subject.placeLabel)
    if (subject.placement.kind === 'near') {
      parts.push(t('weather.notifications.near', distance(subject.placement), place))
    } else {
      parts.push(place)
    }
    parts.push(t('weather.notifications.until', clockTime(WeatherAlertNotificationSubject.expiresAt(subject))))
    // One tag, the one the warning is being called for: a lock screen is not the place for the
    // whole line the alerts card carries.
    const tag = firstTag(subject.warning)
    if (tag != null) parts.push(tag)
    let body = parts.join(' · ')
    if (subject.isLate) body += `\n${t('weather.notifications.late')}`
    return WeatherAlertNotificationContent.make({
      title: tables.eventLabel({ for: subject.warning.event }),
      subtitle: subject.botName != null
        ? t('weather.alerts.source', subject.botName)
        : t('weather.alerts.sourceGeneric'),
      body
    })
  },

  /**
   * "Austin, TX" → "Austin": a notification has one line for the place and the state adds
   * nothing to a town the user chose themselves.
   */
  shortName,

  /** "9:41 PM" on the phone's clock. */
  clockTime,

  /**
   * The one tag worth a line in a notification: what the warning is being called for, in the
   * order spec §10.2 lists them.
   *
   * Computed from the decoded warning's own fields rather than through
   * `MeshWXPresentation.tags`, whose associated-value cases have no settled JS spelling yet; the
   * five numbers and their order are the whole rule.
   */
  firstTag
}

function shortName(label) {
  const comma = label.lastIndexOf(',')
  return comma <= 0 ? label : label.slice(0, comma)
}

function clockTime(date) {
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(date))
}

function distance(placement) {
  const kilometres = placement.kilometres < 0.5
    ? t('weather.unit.underOneKilometre')
    : t('weather.unit.kilometres', Math.round(placement.kilometres))
  // A direction is the wire's compass nibble, 0 to 15, everywhere in the port.
  const direction = MeshWXCompass.abbreviation(placement.direction) ?? String(placement.direction)
  return t('weather.unit.distanceDirection', kilometres, direction)
}

const TORNADO_TAGS = Object.freeze({ 1: 'possible', 2: 'radarIndicated', 3: 'observed' })
const FLOOD_SOURCE_TAGS = Object.freeze({ 1: 'radar', 2: 'radarAndGauge', 3: 'observed' })
const FLOOD_DAMAGE_TAGS = Object.freeze({ 1: 'considerable', 2: 'catastrophic' })

function firstTag(warning) {
  const tornado = TORNADO_TAGS[warning.tornado]
  if (tornado != null) return t('weather.tag.tornado', t(`weather.tornadoTag.${tornado}`))
  const floodSource = FLOOD_SOURCE_TAGS[warning.flood_source]
  if (floodSource != null) return t('weather.tag.floodSource', t(`weather.floodSourceTag.${floodSource}`))
  const floodDamage = FLOOD_DAMAGE_TAGS[warning.flood_damage]
  if (floodDamage != null) return t('weather.tag.floodDamage', t(`weather.floodDamageTag.${floodDamage}`))
  if (warning.hail_qin > 0) return t('weather.tag.hail', (warning.hail_qin / 4).toFixed(2))
  if (warning.wind_mph > 0) return t('weather.tag.wind', warning.wind_mph)
  return null
}

/**
 * Where the app installs its copy, once, at launch.
 *
 * A process-wide holder rather than an injected dependency because the container that owns the
 * notifier is built per connection: installed once, read from anywhere.
 */
let installedCopy = null

export const WeatherAlertNotificationCopyRegistry = {
  install(copy) { installedCopy = copy },

  /** The app's copy, or the table-backed default until `install` has run. */
  get current() { return installedCopy ?? WeatherAlertDefaultCopy }
}
