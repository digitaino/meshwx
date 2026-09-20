// Port of MC1/Views/Tools/Weather/WeatherAlertNotificationsView.swift.
//
// Alert notifications (docs/MESHWX_UI.md §16): the places being watched and what can actually
// reach them, the two things that are not storm warnings, and what this promises — which is less
// than a weather radio, and says so.
//
// Turning a bell *on* happens in Places, beside the place itself; this screen is where the user
// sees whether any of it is working. Nothing here asks the radio for anything: warnings are
// broadcast on `#meshwx` as they happen, so filtering them against a place costs the mesh
// nothing, which is why the feature exists at all.
import { h } from '../kit/dom.js'
import { Card, List, Row, Switch } from '../kit/components.js'
import { icon } from '../kit/icons.js'
import { t } from '../../l10n.js'
import { copy, Line, safeScreen } from './support.js'

export function WeatherAlertNotificationsScreen({ app, page = null }) {
  let onVisible = null

  const refresh = () => {
    const result = app.model?.refreshWatchState?.()
    Promise.resolve(result).then(() => app.nav.refresh()).catch(() => {})
  }

  /**
   * What can arrive right now: the radio is the whole path, so this is its state, and when it is
   * down it says since when only if this visit saw it go.
   */
  const deliveryState = (words) => {
    const model = app.model
    if (model?.isRadioConnected) return t('weather.notifications.connected')
    const since = model?.radioDisconnectedAt ?? null
    if (since == null) return t('weather.notifications.disconnected')
    return t('weather.notifications.disconnectedSince', words.time(since))
  }

  /**
   * My location is matched against the last position the app took, which is only ever while the
   * app was in use: the age is on the row, always, and nothing here says the phone is followed.
   */
  const myLocationAge = (words) => {
    const position = app.model?.myLocationPosition ?? null
    if (position == null) return t('weather.notifications.noPosition')
    return t('weather.notifications.positionAge', words.age(position.timestamp))
  }

  const watchRow = ({ key, title, detail, caption, stop }) => Row({
    key,
    title,
    subtitle: caption == null ? detail : h('span', null, detail, h('span', { class: 'row__break' }), caption),
    chevron: false,
    trailing: h('button', {
      class: 'bell',
      type: 'button',
      'aria-label': t('weather.notifications.stopWatching', title),
      onclick: () => { Promise.resolve(stop()).then(() => app.nav.refresh()).catch(() => {}) },
    }, icon('bell.fill', { size: 20 })),
  })

  return safeScreen({
    id: 'alert-notifications',
    title: () => t('weather.notifications.title'),
    onAppear() {
      refresh()
      // Permission can be turned off in the browser's site settings while the tab is away.
      onVisible = () => { if (document.visibilityState === 'visible') refresh() }
      document.addEventListener('visibilitychange', onVisible)
    },
    onRemove() {
      if (onVisible) document.removeEventListener('visibilitychange', onVisible)
      onVisible = null
    },
    render() {
      const words = copy(app, page)
      const model = app.model
      const subscriptions = model?.subscriptions ?? {}
      const watched = model?.watchedPlaces ?? []
      const watchesMyLocation = model?.isMyLocationWatched === true
      const isWatchingAnything = model?.isWatchingAnything === true

      return List(
        model?.notificationsAuthorization === 'denied'
          ? Card({ key: 'denied' },
            h('div', { class: 'status-line' },
              h('p', null, t('weather.notifications.denied')),
              // iOS opens Settings here. A browser has no such door: the permission belongs to
              // this site and is changed in the browser's own site settings.
              Line(t('web.radio.notifications.browserSettings'))))
          : null,

        Card({ label: t('weather.notifications.placesHeader'), key: 'places' },
          watchesMyLocation
            ? watchRow({
              key: 'myLocation',
              title: t('weather.notifications.myLocation'),
              detail: deliveryState(words),
              caption: myLocationAge(words),
              stop: () => model.setMyLocationWatch(false),
            })
            : null,
          watched.map((place) => watchRow({
            key: place.id,
            title: place.label,
            detail: deliveryState(words),
            caption: null,
            stop: () => model.setWatch(false, { forPlaceID: place.id }),
          })),
          !isWatchingAnything ? Line(t('weather.notifications.empty')) : null),

        Card({ label: t('weather.notifications.alsoHeader'), key: 'also', foot: t('weather.notifications.stormsAlways') },
          Row({
            key: 'otherWarnings',
            title: t('weather.notifications.otherWarnings'),
            subtitle: t('weather.notifications.otherWarningsDetail'),
            chevron: false,
            trailing: Switch({
              checked: subscriptions.notifiesOtherWarnings === true,
              label: t('weather.notifications.otherWarnings'),
              onchange: (value) => { model?.setOtherWarnings?.(value); app.nav.refresh() },
            }),
          }),
          Row({
            key: 'tornadoNearby',
            title: t('weather.notifications.tornadoNearby'),
            subtitle: t('weather.notifications.tornadoNearbyDetail'),
            chevron: false,
            trailing: Switch({
              checked: subscriptions.notifiesTornadoNearby === true,
              label: t('weather.notifications.tornadoNearby'),
              onchange: (value) => { model?.setTornadoNearby?.(value); app.nav.refresh() },
            }),
          })),

        // Deliberately conservative, and revisited only after the on-device test: everything
        // here depends on a radio being in range and this tool still running.
        Card({ key: 'promise' }, Line(t('weather.notifications.promise'))),
      )
    },
  })
}
