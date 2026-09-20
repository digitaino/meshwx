// Port of MC1/Views/Tools/Weather/WeatherCachedView.swift.
//
// Everything this phone kept from `#meshwx`, grouped and counted (docs/MESHWX_UI.md §12).
//
// Reached from one disclosed row at the foot of the weather radio's page and nowhere else. Each
// row names the thing, when its content was taken and when it arrived, and opens the screen that
// shows it in full where there is one. The header says where all of it came from — and that who
// asked for it is not something this phone can know.
import { h } from '../kit/dom.js'
import { Card, List, Row } from '../kit/components.js'
import { t } from '../../l10n.js'
import { copy, safeScreen, Segmented } from './support.js'
import { PendingBar } from './WeatherAskControl.js'
import { WeatherAlertDetailScreen } from './WeatherAlertDetailView.js'

export function WeatherCachedScreen({ app, page }) {
  /**
   * Which way the same pile is read. *Heard on #meshwx* used to be a section of its own on the
   * radio page, above a station count and above this screen's own row, so the page said the same
   * thing three times in three shapes (§3.1 U-16). What was heard and what is cached are one
   * pile: this is the time-ordered view of it.
   */
  let isNewestFirst = false

  const openStation = async (index) => {
    const { WeatherStationScreen } = await import('../place/index.js')
    app.nav.push(WeatherStationScreen({ app, page, index }))
  }

  const rowFor = (item, words) => {
    const title = words.channelSubject(item.subject, app.tables)
    const subtitle = words.channelTimes(item.contentAt ?? null, item.receivedAt)
    const destination = item.destination ?? null
    if (destination?.kind === 'station') {
      return Row({ key: item.id, title, subtitle, onclick: () => { openStation(destination.value).catch(() => {}) } })
    }
    if (destination?.kind === 'alert') {
      return Row({
        key: item.id,
        title,
        subtitle,
        onclick: () => app.nav.push(WeatherAlertDetailScreen({ app, page, identity: destination.value })),
      })
    }
    // A reply nobody here asked for names only its subject, so it opens nothing rather than a
    // station screen picked by guesswork.
    return Row({ key: item.id, title, subtitle })
  }

  return safeScreen({
    id: 'cached',
    title: () => t('weather.cache.title'),
    render() {
      const words = copy(app, page)
      const snapshot = page?.snapshot ?? null
      const heard = snapshot?.heard ?? []
      const groups = snapshot?.cache?.groups ?? []

      return List(
        Card({ key: 'intro' },
          h('div', { class: 'status-line' },
            h('p', { class: 'line' }, t('weather.cache.intro')),
            heard.length > 0
              ? Segmented({
                label: t('weather.cache.title'),
                value: isNewestFirst,
                options: [
                  { value: false, label: t('weather.cache.byKind') },
                  { value: true, label: t('weather.cache.newestFirst') },
                ],
                onchange: (next) => { isNewestFirst = next; app.nav.refresh() },
              })
              : null)),

        isNewestFirst
          // What the channel carried recently, newest first: the scheduled broadcasts and the
          // answers, in one list and not told apart. On a broadcast channel they are the same
          // thing, and separating them would be a claim about who asked.
          ? Card({ key: 'heard', foot: t('weather.heard.footer') }, heard.map((item) => rowFor(item, words)))
          : groups.map((group) => Card({
            key: `group-${group.group}`,
            label: t('weather.cache.group', words.cacheGroup(group.group), group.items.length),
          }, group.items.map((item) => rowFor(item, words)))),

        PendingBar({ app, requestsOnScreen: [] }),
      )
    },
  })
}
