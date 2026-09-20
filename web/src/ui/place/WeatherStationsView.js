// Port of MC1/Views/Tools/Weather/WeatherStationsView.swift
//
// The airport stations whose readings this device holds, and one station's reading in full
// (docs/MESHWX_UI.md §12).
//
// Both screens are built from **the page they were opened from** (§3.1 U-8, U-18): the order, the
// distances and the radio they name are that place's, whatever the pager has since been swiped to.
// A station screen says each fact once — the station's name is the headline, its distance from the
// page is one line, and the message the reading arrived in is another; those two used to be
// printed three times over between them.

import { h } from '../kit/dom.js'
import { icon, hasIcon } from '../kit/icons.js'
import { Card, List, Row } from '../kit/components.js'
import { t } from '../../l10n.js'
import { MeshWXPresentation, MeshWXStationObservation, MeshWXTables, MeshWXWindReading } from '../../meshwx/index.js'
import { WeatherRequest, WeatherStoredObservation, WeatherTextAssembly } from '../../weather/index.js'
import { WeatherGeo, WeatherNames, WeatherUpdatePlan } from '../../screen/index.js'
import { WeatherUpdateControl } from './WeatherUpdateControl.js'
import { WeatherAskButton, WeatherPendingBar, requestKeys } from './WeatherAskControl.js'
import {
  age, attempt, clockTime, condition as conditionWord, distance as distanceText, isNight, nowOf, placeNameOf,
  pressure, screenFor, screenValue, sentenceStart, sourceNameOf, stationReport, temperature, visibility,
  wind as windText,
} from './support.js'

/**
 * The list of stations (docs/MESHWX_UI.md §12), in two sections named for where the reading came
 * from — never for who asked, which this device does not record.
 *
 * @param {object} options.page  the `WeatherPageScreen` (or page id) this list was opened from
 */
export function WeatherStationsScreen({ app, page }) {
  const current = () => screenFor(app, page)

  return {
    id: 'stations',
    title: () => t('weather.stations.title'),
    render: () => {
      const screen = current()
      const readings = screen?.snapshot?.readings ?? []
      const scheduled = readings.filter((one) => one.isInFootprint)
      const answers = readings.filter((one) => !one.isInFootprint)
      return List(
        Card({}, h('p', { class: 'card__note' },
          t('weather.stations.intro', sentenceStart(sourceNameOf(screen))))),
        scheduled.length === 0
          ? null
          : Card({ label: t('weather.stations.scheduled') },
            scheduled.map((reading) => stationRow({ app, screen, reading, page }))),
        answers.length === 0
          ? null
          : Card({ label: t('weather.stations.answers') },
            answers.map((reading) => stationRow({ app, screen, reading, page }))),
        WeatherPendingBar({ app, requestsOnScreen: new Set() }))
    },
  }
}

function stationRow({ app, screen, reading, page }) {
  const observation = reading?.stored?.observation ?? null
  const now = nowOf(screen, app)
  const observedAt = attempt(() => WeatherStoredObservation.observedAt(reading.stored))
  const night = isNight(observedAt, { timeZone: app?.timeZone }) === true
  const symbol = attempt(() => MeshWXPresentation.observationSymbolName({ for: observation?.sky, isNight: night }))

  const meta = []
  if (reading.distanceKilometres != null) {
    const text = distanceText(reading.distanceKilometres, { direction: reading.direction })
    if (text) meta.push(text)
  }
  meta.push(reading.isStale
    ? age(observedAt, { now })
    : t('weather.stations.asOf', clockTime(observedAt, { now, timeZone: app?.timeZone, locale: app?.locale }) ?? ''))
  // A reading that came from another radio says so: the intro names the one this page asks.
  if (reading.botID !== screen?.snapshot?.source?.botID) {
    const name = attempt(() => screen?.model?.botName?.(reading.botID))
    if (name) meta.push(t('weather.stations.fromBot', name))
  }

  return Row({
    key: String(reading.index),
    icon: symbol != null ? icon(hasIcon(symbol) ? symbol : 'cloud', { size: 22 }) : null,
    title: attempt(() => WeatherNames.stationName(reading.station?.name)) ?? '',
    subtitle: meta.filter(Boolean).join(' · '),
    value: observation?.temp_f == null ? null : temperature(observation.temp_f, { locale: app?.locale }),
    stale: reading.isStale === true,
    onclick: () => app?.nav?.push?.(WeatherStationScreen({ app, page: screen ?? page, index: reading.index })),
  })
}

/**
 * One station's reading in full, why it is as old as it is, and its coded airport reports on
 * request (docs/MESHWX_UI.md §12).
 *
 * Built from the station rather than from a reading, so a station nothing has ever arrived for has
 * a screen too — reached from a Places search by airport code, which saves nothing at all.
 *
 * @param {object} options.page   the page this station was opened from
 * @param {number} options.index  the station's wire index
 */
export function WeatherStationScreen({ app, page, index }) {
  const current = () => screenFor(app, page)
  const tables = () => app?.tables ?? attempt(() => MeshWXTables.shared)
  const station = () => attempt(() => tables()?.station({ at: index })) ?? null
  const readingOf = (screen) => (screen?.snapshot?.readings ?? []).find((one) => one.index === index) ?? null
  const planOfStation = (screen) =>
    attempt(() => screen?.model?.updatePlan?.({ forStation: index, in: screen.snapshot })) ?? WeatherUpdatePlan.empty

  return {
    id: `station-${index}`,
    title: () => attempt(() => WeatherNames.stationName(station()?.name)) ?? t('weather.stations.title'),
    render: () => {
      const screen = current()
      const found = station()
      if (found == null) return List(Card({}, h('p', { class: 'card__note' }, t('weather.station.nothingHeld'))))
      const reading = readingOf(screen)
      const plan = planOfStation(screen)
      return List(
        summary({ app, screen, station: found, reading }),
        Card({}, h('div', { class: 'card__block' },
          WeatherUpdateControl({ app, screen, plan, showsCaption: true }))),
        reading == null ? null : values({ app, screen, reading }),
        airportReports({ app, screen, station: found }),
        WeatherPendingBar({ app, requestsOnScreen: onScreen({ screen, plan, station: found }) }))
    },
  }
}

function onScreen({ screen, plan, station }) {
  const planned = attempt(() => WeatherUpdatePlan.requests(plan)) ?? []
  const sent = screenValue(screen, 'updateRequests', (one) => one.model?.updateRequests?.({ pageID: one.pageID })) ?? []
  const coded = station?.icao == null
    ? []
    : [WeatherRequest.metar({ station: station.icao }), WeatherRequest.taf({ station: station.icao })]
  return requestKeys([...planned, ...sent, ...coded])
}

function summary({ app, screen, station, reading }) {
  const place = screen?.snapshot?.place ?? null
  const name = placeNameOf(screen)
  const now = nowOf(screen, app)
  const observedAt = reading == null ? null : attempt(() => WeatherStoredObservation.observedAt(reading.stored))

  // How far it is **from the page this screen was opened on**, named — the one distance line on
  // the screen (§3.1 U-8). Always the station's true distance, never a capped one.
  let fromPlace = null
  if (place != null && name != null) {
    const coordinate = { latitude: station.lat, longitude: station.lon }
    const km = attempt(() => WeatherGeo.kilometres(place.coordinate, coordinate))
    const direction = attempt(() => WeatherGeo.direction({ from: place.coordinate, to: coordinate }))
    const text = km == null ? null : distanceText(km, { direction })
    if (text) fromPlace = t('weather.stations.fromPlace', text, name)
  }

  return Card({}, h('div', { class: 'card__block station-summary' },
    h('p', { class: 'station-summary__name' }, attempt(() => WeatherNames.stationName(station.name)) ?? ''),
    h('p', { class: 'footnote' }, [station.icao, station.state].filter(Boolean).join(' · ')),
    fromPlace ? h('p', { class: 'footnote' }, fromPlace) : null,
    // Which message this reading arrived in, which nothing else on the screen says.
    reading == null
      ? h('p', { class: 'card__note' }, t('weather.station.nothingHeld'))
      : [
        h('p', { class: 'footnote' }, stationReport({
          botName: attempt(() => screen?.model?.botName?.(reading.botID)) ?? sourceNameOf(screen),
          reportedAt: observedAt,
          now,
          timeZone: app?.timeZone,
          locale: app?.locale,
        }) ?? ''),
        reading.isStale ? h('p', { class: 'footnote footnote--warn' }, age(observedAt, { now }) ?? '') : null,
        // Why it is as old as it is: the bot's hourly report does not carry this station, so
        // nothing refreshes it until somebody asks for it by name.
        reading.isInLatestBatch ? null : h('p', { class: 'footnote' }, t('weather.station.notInBatch')),
      ]))
}

function values({ app, screen, reading }) {
  const observation = reading.stored?.observation ?? null
  if (observation == null) return null
  const locale = app?.locale
  const now = nowOf(screen, app)
  const observedAt = attempt(() => WeatherStoredObservation.observedAt(reading.stored))
  const rows = []
  const line = (title, value) => { if (value != null && value !== '') rows.push(Row({ key: title, title, value })) }

  line(t('weather.station.temperature'), observation.temp_f == null ? null : temperature(observation.temp_f, { locale }))
  const feelsLike = attempt(() => MeshWXStationObservation.feelsLikeF(observation))
  if (observation.feels_delta_f !== 0 && feelsLike != null) {
    line(t('weather.station.feelsLike'), temperature(feelsLike, { locale }))
  }
  line(t('weather.station.sky'), conditionWord(observation.sky))
  line(t('weather.station.wind'), windText(attempt(() => MeshWXWindReading.make({ observation }))))
  line(t('weather.station.humidity'), observation.humidity_pct == null
    ? null
    : t('weather.station.percent', Math.trunc(observation.humidity_pct)))
  line(t('weather.station.dewPoint'), observation.dewpoint_f == null
    ? null
    : temperature(observation.dewpoint_f, { locale }))
  line(t('weather.station.pressure'), observation.pressure_inhg == null
    ? null
    : t('weather.station.inchesOfMercury', pressure({ inchesOfMercury: observation.pressure_inhg, locale }) ?? ''))
  line(t('weather.station.visibility'), observation.visibility_mi == null
    ? null
    : visibility({ miles: observation.visibility_mi, locale }))

  if (rows.length === 0) return null
  // When the report was taken, on the bot's clock (§12).
  const asOf = clockTime(observedAt, { now, timeZone: app?.timeZone, locale })
  return Card({ foot: asOf == null ? null : t('weather.stations.asOf', asOf) }, rows)
}

/**
 * METAR and TAF come back as coded text and leave the reading untouched, so they keep their own
 * buttons: they are not what Update plans for (§8, §11). The Update control above already carries
 * the reason nothing can be asked, so these two say it no further (§3.1 U-24).
 */
function airportReports({ app, screen, station }) {
  const icao = station?.icao
  if (icao == null || screen == null) return null
  const metar = WeatherRequest.metar({ station: icao })
  const taf = WeatherRequest.taf({ station: icao })
  return Card({ label: t('weather.station.airportReports') },
    h('div', { class: 'card__block' },
      WeatherAskButton({ app, screen, title: t('weather.request.askMetar'), request: metar, showsBlockReason: false }),
      ownText({ app, screen, request: metar }),
      WeatherAskButton({ app, screen, title: t('weather.request.askTaf'), request: taf, showsBlockReason: false }),
      ownText({ app, screen, request: taf })))
}

function ownText({ app, screen, request }) {
  const item = (screen?.snapshot?.texts ?? []).find((one) =>
    attempt(() => WeatherRequest.isEqual(one.assembly?.request, request)) === true)
  if (item == null) return null
  const received = clockTime(item.assembly?.lastReceivedAt, {
    now: nowOf(screen, app), timeZone: app?.timeZone, locale: app?.locale,
  })
  return h('div', { class: 'station-report' },
    h('pre', { class: 'narrative narrative--mono' }, reportBody(item.assembly)),
    received ? h('p', { class: 'footnote' }, t('weather.reports.received', received)) : null)
}

/**
 * `WeatherReportText.body` — the chunks in order, with the missing-part marker where one never
 * arrived. One line of the other engineer's product screen, repeated here because the coded
 * reports live on this screen (§12).
 */
function reportBody(assembly) {
  const chunks = attempt(() => WeatherTextAssembly.orderedChunks(assembly)) ?? []
  return chunks.map((chunk) => chunk ?? t('weather.reports.missingPart')).join('')
}
