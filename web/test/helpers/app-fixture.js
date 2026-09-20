// What the app-layer suites share: the Swift fixture's clock, calendar and locale, a model built
// on a `FakeWeatherHost`, and the small values the Swift tests construct inline.
//
// Port of the `WeatherFormattingTests` / `WeatherCopyTests` fixtures of
// MC1Tests/Views/Tools/Weather, plus the two the model suite needs.

import { MeshWXGeometry, MeshWXSky, MeshWXTables } from '../../src/meshwx/index.js'
import { nodeBundleLoader } from '../../src/meshwx/nodeLoader.js'
import { WeatherBotState, WeatherStoredWarning } from '../../src/weather/index.js'
import { FakeWeatherHost, WeatherDefaults, WeatherToolModel } from '../../src/app/index.js'

/** Load the bundle once for a whole `node --test` process. */
let tablesPromise = null
export function loadTables() {
  if (tablesPromise == null) tablesPromise = MeshWXTables.load(nodeBundleLoader())
  return tablesPromise
}

/** The outlines, parsed once per process (docs/PORTING.md §8). */
let geometryPromise = null
export function loadGeometry() {
  if (geometryPromise == null) {
    MeshWXGeometry.configure(nodeBundleLoader())
    geometryPromise = MeshWXGeometry.shared.preload().then(() => MeshWXGeometry.shared)
  }
  return geometryPromise
}

/** 2026-09-14 23:20 CDT, in milliseconds: `WeatherFormattingTests.now`. */
export const now = 1_789_446_000 * 1000

/** The Swift fixture's `Calendar` and `Locale`, as PORTING.md spells them. */
export const timeZone = 'America/Chicago'
export const locale = 'en-US'

/** ICU puts a narrow no-break space before AM/PM. */
export function plain(text) {
  return text == null ? text : text.replace(/ /g, ' ').replace(/ /g, ' ')
}

/** 11:02 PM and 8:02 PM on the fixture's evening. */
export const elevenOhTwo = now - 18 * 60 * 1000
export const eightOhTwo = now - 198 * 60 * 1000

export const austin = place({ latitude: 30.2672, longitude: -97.7431, label: 'Austin, TX' })
export const dallas = place({ latitude: 32.7767, longitude: -96.797, label: 'Dallas, TX' })
export const roundRock = place({
  latitude: 30.5083,
  longitude: -97.6789,
  label: 'Round Rock, TX',
  kind: 'searched',
  uncertaintyKilometres: 5,
})

export function place({
  latitude,
  longitude,
  label,
  kind = 'current',
  uncertaintyKilometres = 0.5,
  locatedAt = now,
}) {
  return {
    kind,
    coordinate: { latitude, longitude },
    label,
    uncertaintyKilometres,
    locatedAt: kind === 'current' ? locatedAt : null,
    zipCode: null,
    searchedAs: 'town',
  }
}

/** A decoded station observation, the shape §6 of the vectors gives. */
export function observation({
  station,
  tempF = null,
  sky = MeshWXSky.few,
  windDegrees = 0,
  windMph = 0,
  gustMph = 0,
}) {
  return {
    station,
    temp_f: tempF,
    dewpoint_f: null,
    wind_dir_deg: windDegrees,
    wind_dir: 'N',
    sky,
    wind_mph: windMph,
    gust_mph: gustMph,
    visibility_mi: null,
    pressure_inhg: null,
    humidity_pct: null,
    feels_delta_f: 0,
    age_min: null,
  }
}

/** A decoded warning (PORTING.md §5). */
export function warning({
  event,
  office = 35,
  etn,
  expiresMinutes = Math.floor(now / 60000) + 60,
  polygon = null,
  areas = null,
  tornado = 0,
  floodSource = 0,
  floodDamage = 0,
  hailQuarterInches = 0,
  windMph = 0,
  issuedMinutes = null,
}) {
  return {
    seq: 0,
    bot: 0x041d,
    type: 1,
    name: 'warning',
    flags: 0,
    event,
    office,
    etn,
    expires_min: expiresMinutes,
    tornado,
    flood_source: floodSource,
    flood_damage: floodDamage,
    hail_qin: hailQuarterInches,
    wind_mph: windMph,
    update: false,
    source: 0,
    polygon,
    areas,
    issued_min: issuedMinutes,
  }
}

/** `WeatherCopyTests.warning`: a warning with a box around one latitude. */
export function storedWarning({ event, etn, minutes = 60, polygonAt, longitude = -97.74, areas = null }) {
  const polygon = [
    [polygonAt + 0.05, longitude - 0.06],
    [polygonAt + 0.05, longitude + 0.06],
    [polygonAt - 0.05, longitude + 0.06],
    [polygonAt - 0.05, longitude - 0.06],
  ]
  const expires = Math.floor((now / 1000 + minutes * 60) / 60)
  return WeatherStoredWarning.make({
    warning: warning({ event, etn, expiresMinutes: expires, polygon, areas }),
    receivedAt: now,
  })
}

/** A bot state holding the given warnings, keyed by identity the way the reducer keys them. */
export function statesHolding(warnings, { botID = 0x041d } = {}) {
  const state = WeatherBotState.make({ botID })
  for (const stored of warnings) {
    state.warnings[`${stored.warning.event}.${stored.warning.office}.${stored.warning.etn}`] = stored
  }
  return { [String(botID)]: state }
}

/**
 * A model on a fake host with in-memory defaults, and a clock that is also its scheduler.
 *
 * The Swift tests construct `WeatherToolModel()` with no `AppState` and never call `attach`; the
 * same is true here — `start()` is only called by the suites that need the visit running.
 */
export function makeModel({ host = new FakeWeatherHost(), clock = new TestClock(now), ...options } = {}) {
  const model = new WeatherToolModel({
    host,
    tables: MeshWXTables.shared,
    geometry: MeshWXGeometry.shared,
    locale,
    timeZone,
    now: () => clock.now,
    setTimeout: (fn, ms) => clock.setTimeout(fn, ms),
    clearTimeout: (id) => clock.clearTimeout(id),
    scheduleNotify: (fn) => {
      let cancelled = false
      queueMicrotask(() => {
        if (!cancelled) fn()
      })
      return () => {
        cancelled = true
      }
    },
    defaults: new WeatherDefaults(),
    ...options,
  })
  return { model, host, clock }
}

/** A settable clock that is also the model's scheduler (`WeatherTestClock`, for this layer). */
export class TestClock {
  constructor(start = now) {
    this.now = start
    this.timers = new Map()
    this.nextID = 1
  }

  setTimeout(fn, milliseconds) {
    const id = this.nextID
    this.nextID += 1
    this.timers.set(id, { at: this.now + milliseconds, fn })
    return id
  }

  clearTimeout(id) {
    this.timers.delete(id)
  }

  /** Moves the clock forward, firing every timer that falls due and everything it re-arms. */
  async advance(seconds) {
    const target = this.now + seconds * 1000
    for (;;) {
      let due = null
      for (const [id, timer] of this.timers) {
        if (timer.at <= target && (due == null || timer.at < due.timer.at)) due = { id, timer }
      }
      if (due == null) break
      this.timers.delete(due.id)
      this.now = Math.max(this.now, due.timer.at)
      await due.timer.fn()
      await flush()
    }
    this.now = target
    await flush()
  }
}

/** Drains the microtask queue, so everything the model chained has run. */
export async function flush(turns = 8) {
  for (let turn = 0; turn < turns; turn += 1) {
    await new Promise((resolve) => setImmediate(resolve))
  }
}
