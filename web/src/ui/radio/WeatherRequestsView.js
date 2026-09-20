// The whole request log (docs/MESHWX_UI.md §12), pushed from "All requests (27)" on the radio
// page.
//
// Owner, 20 September 2026: *"Your requests is way too long of a list."* So the card on the radio
// page keeps the newest three (`model.requestLogSplit.newest`) and everything else lives here,
// one screen away. The rows are identical either side of the push — the same `RequestRow` — so
// the row a reader tapped past is the row they find.
//
// This phone's own requests and nothing else: an answer on `#meshwx` reaches every phone listening
// and carries no requester, so the only requests that can honestly be listed are the ones this app
// sent. An answer served from the five-minute rule is not one — nothing went out — and the log
// does not hold it.
import { Card, List, Row } from '../kit/components.js'
import { t } from '../../l10n.js'
import { copy, Line, safeScreen } from './support.js'

/**
 * One request this phone put on the air: what it asked for, when, and how it ended.
 *
 * `words` is `copy(app, page)` — the page's own clock, so a pushed screen dates a row the way the
 * page it was opened from would (§3.1 P-1).
 */
export function RequestRow({ entry, words, tables }) {
  return Row({
    key: entry.id,
    title: words.requestName(entry.request, tables),
    subtitle: `${words.time(entry.sentAt)} · ${words.requestOutcome(entry.outcome)}`,
    chevron: false,
  })
}

/** `WeatherRequestsScreen({ app, page })` — every request, newest first. */
export function WeatherRequestsScreen({ app, page }) {
  return safeScreen({
    id: 'requests',
    title: () => t('weather.requests.allTitle'),
    render() {
      const words = copy(app, page)
      const all = app.model?.requestLogSplit?.all ?? []
      return List(
        Card({ key: 'requests', foot: t('weather.requests.footer') },
          all.length === 0
            ? Line(t('weather.requests.none'))
            : all.map((entry) => RequestRow({ entry, words, tables: app.tables }))),
      )
    },
  })
}
