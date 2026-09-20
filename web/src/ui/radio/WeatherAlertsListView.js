// Port of MC1/Views/Tools/Weather/WeatherAlertsListView.swift.
//
// Every alert in the source bot's area (docs/MESHWX_UI.md §12): a map, the alerts by where they
// are relative to **this page's** place, the §7.4 status line, and what the alert list named but
// never arrived.
import { h } from '../kit/dom.js'
import { Card, List, Row } from '../kit/components.js'
import { t } from '../../l10n.js'
import { MeshWXWarningIdentity } from '../../meshwx/index.js'
import { copy, safeScreen } from './support.js'
import { AskButton, PendingBar } from './WeatherAskControl.js'
import { AlertRow, AlertStatusRow } from './WeatherAlertRow.js'
import { alertsPrint, drawWhenChanged, MapCard, WeatherAlertFullMapScreen, WeatherMapDrawing } from './WeatherAlertMap.js'
import { pictureOf, WeatherAreaMapCopy, WeatherAreaMapScreen } from './WeatherAreaMapView.js'
import { WeatherAlertDetailScreen } from './WeatherAlertDetailView.js'

/** A Swift `Set<MeshWXWarningIdentity>` however it was ported: identities, or their keys. */
function keySet(value) {
  const out = new Set()
  for (const one of value ?? []) out.add(typeof one === 'string' ? one : MeshWXWarningIdentity.key(one))
  return out
}

export function WeatherAlertsListScreen({ app, page }) {
  const state = { map: null, drawn: null, app }
  const title = () => t('weather.alertsList.title', page?.sourceName ?? '')

  const statusLine = (snapshot) => {
    if (snapshot == null) return null
    return copy(app, page).alertStatus(snapshot.alertStatus, {
      source: page.sourceName,
      place: snapshot.place,
      areaName: page.context?.placeCounty?.name ?? null,
    })
  }

  /** When the newest part was built, or that nobody has asked for one — the usual answer. */
  const areaMapDetail = () => {
    // Revision 10 resolves every held sweep into one picture; this row says when the newest part
    // of it was built, and the map screen itself says the rest, part by part.
    return WeatherAreaMapCopy.pictureLine(pictureOf(app, page), copy(app, page))
  }

  const section = (label, items, { showsLocation = false } = {}) => {
    if (items.length === 0) return null
    const words = copy(app, page)
    return Card({ label, key: `section-${label}` },
      items.map((item) => AlertRow({
        app,
        page,
        item,
        key: MeshWXWarningIdentity.key(item.identity),
        location: showsLocation ? words.alertLocation(item.warning, page.snapshot?.place ?? null, app.tables) : null,
        onclick: () => app.nav.push(WeatherAlertDetailScreen({ app, page, identity: item.identity })),
      })))
  }

  return safeScreen({
    id: 'alerts-list',
    title,
    render() {
      const snapshot = page?.snapshot ?? null
      const alerts = snapshot?.alerts ?? []
      const words = copy(app, page)
      const line = statusLine(snapshot)
      const alertsRequest = snapshot != null ? page.alertsRequest?.({ for: snapshot.alertStatus }) ?? null : null
      const statusAsks = line?.action === 'askForAlerts'
      const missing = page?.missingWarnings ?? []
      const missingRequest = page?.missingWarningsRequest ?? null
      const notAvailable = keySet(page?.missingNotAvailable)
      const sourceLine = snapshot?.source == null
        ? t('weather.alerts.sourceGeneric')
        : t('weather.alerts.source', page.sourceName)

      drawWhenChanged(state, alertsPrint(app, alerts, snapshot?.place ?? null, true), () => WeatherMapDrawing.make({
        warnings: alerts.map((item) => item.warning),
        place: snapshot?.place ?? null,
        framesPlace: true,
        tables: app.tables,
        geometry: app.geometry,
      }))

      const sections = snapshot?.place == null
        ? [section(t('weather.alertsList.all', page?.sourceName ?? ''), alerts, { showsLocation: true })]
        : [
          section(t('weather.alertsList.here'), alerts.filter((item) => item.placement?.kind === 'here')),
          section(t('weather.alertsList.unsure'), alerts.filter((item) => item.placement?.kind === 'checking' || item.placement?.kind === 'unplaced')),
          section(t('weather.alertsList.near'), alerts.filter((item) => item.placement?.kind === 'near')),
          section(t('weather.alertsList.elsewhere'), alerts.filter((item) => item.placement?.kind === 'elsewhere'), { showsLocation: true }),
        ]

      return List(
        MapCard({
          app,
          state,
          label: t('weather.alertsList.openMap'),
          onOpen: () => app.nav.push(WeatherAlertFullMapScreen({ app, page, title: title() })),
        }),
        // The alert map, one screen away (docs/MESHWX_UI.md §17). A row rather than anything that
        // loads: opening it asks for nothing, and the map it shows is whatever the channel has
        // already carried — the country, a few states, or nothing at all.
        Card({ key: 'area-map' },
          Row({
            title: t('weather.areaMap.title'),
            subtitle: areaMapDetail(),
            onclick: () => app.nav.push(WeatherAreaMapScreen({ app, page })),
          })),

        sections,

        Card({ key: 'status' },
          line != null
            ? AlertStatusRow({ app, page, line, source: sourceLine, request: alertsRequest, showsAskFootnotes: statusAsks })
            : h('div', { class: 'status-line' }, h('p', { class: 'line line--quiet' }, sourceLine))),

        missing.length > 0
          ? Card({ label: t('weather.alertsList.listedHeader'), key: 'missing' },
            missing.map((identity) => h('div', { class: 'row', key: MeshWXWarningIdentity.key(identity) },
              h('span', { class: 'row__main' },
                h('span', { class: 'row__title' }, words.eventName(identity.event, app.tables)),
                h('span', { class: 'row__subtitle line--warn' },
                  notAvailable.has(MeshWXWarningIdentity.key(identity))
                    ? t('weather.alertsList.notAvailable', page.sourceName)
                    : t('weather.alertsList.notReceived'))))),
            // One warning per tap, named on the button, so each tap visibly asks for the next one.
            missingRequest != null
              ? AskButton({
                app,
                page,
                title: words.askAlertsTitle(missingRequest, app.tables),
                request: missingRequest,
                showsFootnotes: !statusAsks,
              })
              : null)
          : null,

        PendingBar({ app, requestsOnScreen: [statusAsks ? alertsRequest : null, missingRequest].filter(Boolean) }),
      )
    },
  })
}
