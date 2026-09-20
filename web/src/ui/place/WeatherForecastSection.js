// Port of MC1/Views/Tools/Weather/WeatherForecastSection.swift
//
// The forecast (docs/MESHWX_UI.md §9): the forecast for the point nearest the place, one row per
// day or day-and-night, labelled against now.
//
// It is a card on the place's page, **not a card that opens one** (§3.2 Q3). Its label carries the
// one time it has — "FORECAST · ISSUED 4:02 PM", the small-capitals row *inside* the card
// (§3.1.2 V-3) — and its last line names the point the rows are for and how far that point is from
// the place (§3.1 U-9). The place is named by the title bar and never repeated here. Nothing here
// asks the radio: a missing or stale forecast is what Update plans for (§11).

import { h } from '../kit/dom.js'
import { icon, hasIcon } from '../kit/icons.js'
import { Card } from '../kit/components.js'
import { t } from '../../l10n.js'
import { MeshWXPresentation } from '../../meshwx/index.js'
import { WeatherStoredForecast } from '../../weather/index.js'
import { WeatherForecastCard, WeatherNames } from '../../screen/index.js'
import {
  age, attempt, clockTime, dataSource, forecastMissing, forecastPointChosenByBot, hazards,
  kilometres, nowOf, placeNameOf, rainChance, rowLabel, sourceNameOf, temperatures,
} from './support.js'

/** The card, as a Node. */
export function WeatherForecastSection({ app, screen }) {
  const forecast = screen?.snapshot?.forecast ?? null
  const title = t('weather.forecast.titleGeneric')
  const summary = forecast?.kind === 'forecast' ? forecast.value : null

  return Card(
    { label: title, labelTrailing: issuedText({ app, screen, summary }), foot: foot({ summary, screen }) },
    body({ app, screen, forecast, summary }))
}

function body({ app, screen, forecast, summary }) {
  switch (forecast?.kind) {
    case 'noPlace':
      return note(t('weather.place.choosePrompt'))
    case 'missing':
      return note(forecastMissing({
        placeName: placeNameOf(screen) ?? '',
        point: forecast.point,
        kilometres: forecast.kilometres,
      }))
    case 'forecast':
      return (summary?.rows ?? []).map((row) =>
        WeatherForecastRowView({ row, locale: app?.locale, timeZone: app?.timeZone }))
    default:
      return null
  }
}

function note(text) {
  if (!text) return null
  return h('p', { class: 'card__note' }, text)
}

/** "issued 4:02 PM", or "issued 14 h ago" once it is stale. Nothing until there is a forecast. */
function issuedText({ app, screen, summary }) {
  if (summary == null) return null
  const issuedAt = attempt(() => WeatherStoredForecast.issuedAt(summary.stored))
  if (issuedAt == null) return null
  const now = nowOf(screen, app)
  const when = summary.isStale
    ? age(issuedAt, { now })
    : clockTime(issuedAt, { now, timeZone: app?.timeZone, locale: app?.locale })
  return when == null ? null : t('weather.forecast.issued', when)
}

/**
 * "Austin Camp Mabry · 6 km", the distance left out when the point is the place, and
 * "· heard on #meshwx" when somebody else on the channel asked for it (§3.1 U-14). Under it, in
 * the same voice, where the bot got the forecast (§12.1) — and nothing at all when it did not say.
 */
function foot({ summary, screen }) {
  if (summary == null) return null
  // A point the bot chose has no name in this bundle, so what the foot can honestly say is
  // where it came from (spec §7, revision 10).
  const chosen = summary.source?.kind === 'chosenByBot'
  const parts = [chosen
    ? (forecastPointChosenByBot({ source: sourceNameOf(screen) ?? '' }) ?? '')
    : (attempt(() => WeatherNames.pointLabel(summary.point?.name)) ?? '')]
  if ((summary.kilometres ?? 0) >= 1) {
    const distance = kilometres(summary.kilometres)
    if (distance) parts.push(distance)
  }
  if (attempt(() => WeatherForecastCard.isOwn(summary)) === false) parts.push(t('weather.reports.overheard'))
  const source = dataSource(summary.stored?.source)
  return [
    h('div', null, parts.filter(Boolean).join(' · ')),
    source ? h('div', null, source) : null,
  ]
}

/** One forecast row: label, icon, rain chance, temperatures, hazards. */
export function WeatherForecastRowView({ row, locale, timeZone }) {
  const glyph = attempt(() => MeshWXPresentation.icon({ for: row, isNight: row.isNightIcon === true }))
  const label = rowLabel(row.label, { timeZone, locale })
  const temps = temperatures({ highF: row.highF, lowF: row.lowF, locale })
  const rain = rainChance(row)
  const hazard = hazards(row)
  const symbol = glyph?.symbolName ?? null

  return h('div', { class: 'row forecast-row', key: row.id ?? label },
    h('span', { class: 'forecast-row__day' }, label ?? ''),
    // No wind accent beside the glyph: this icon set has no wind mark, and `WeatherCopy.hazards`
    // already writes "Windy" on the row's second line for the same flag.
    h('span', { class: 'forecast-row__icon' },
      symbol != null ? icon(hasIcon(symbol) ? symbol : 'cloud', { size: 22 }) : null),
    h('span', { class: 'forecast-row__pop' }, rain ?? ''),
    h('span', { class: 'forecast-row__temps' }, temps ?? ''),
    hazard ? h('span', { class: 'forecast-row__note' }, hazard) : null)
}
