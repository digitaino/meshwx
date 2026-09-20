// Port of MC1/Views/Tools/Weather/WeatherPlacePickerView.swift
//
// Places (docs/MESHWX_UI.md §12): the one screen that is about the tool rather than about a place,
// and therefore **the only sheet in the tool** (§3.1 U-11). My location first with what is held
// for it, then the places kept on this device — each with its distance, its reading, a bell, a way
// to remove it and a way to reorder it — then a search that takes a town, a ZIP or an airport
// code, and, quietly last, the places heard on the channel in the last day.
//
// **One Done** (§3.1 U-10): "Cancel" implied a commit step that does not exist, because every
// change here is already saved. Removing and reordering live under a plain **Edit**, in the
// leading slot, because a removal nobody can find is a removal that does not exist (§3.1 U-30).
// There are no swipe actions here: a swipe on a touch screen is how the pager behind this sheet
// changes place, and on a desktop it is nothing at all. Edit shows a Remove on every row, drag
// handles for a mouse, and **move up / move down buttons**, which are the same two edits from a
// keyboard.
//
// **Distances are the user's own** (§3.1 U-8), whatever page the pager is on; only with no fix at
// all do they borrow the page's, and then the section says so.
//
// Picking anything sends nothing (§3.1 O-4, overturning O-1): Update is on the screen behind and
// says what it would ask for. An airport code opens that station's screen and saves nothing at
// all (§3.1 U-3).

import { h } from '../kit/dom.js'
import { icon } from '../kit/icons.js'
import { Button, Card, Row } from '../kit/components.js'
import { t } from '../../l10n.js'
import { MeshWXGeo, MeshWXTables, MeshWXZip } from '../../meshwx/index.js'
import {
  WeatherNames, WeatherPage, WeatherPlace, WeatherPlaceRowReading, WeatherPlaceSearch, WeatherSavedPlace,
} from '../../screen/index.js'
import {
  afterNavigationSettles, ago, attempt, kilometres, placeName, placeRowText, screenFor,
} from './support.js'

/** Opens Places. Returns the sheet handle. */
export function openPlaces({ app }) {
  return WeatherPlacePickerView({ app })
}

/**
 * Presents the sheet and returns its handle.
 *
 * @param {object} options
 * @param {object} options.app
 */
export function WeatherPlacePickerView({ app }) {
  const state = { query: '', applied: '', isEditing: false, timer: null, input: null, dragID: null }
  let handle = null
  const refresh = () => handle?.refresh?.()

  const model = () => app?.model ?? null
  const tables = () => app?.tables ?? attempt(() => MeshWXTables.shared)
  const now = () => attempt(() => model()?.now) ?? Date.now()

  const savedPlaces = () => attempt(() => model()?.savedPlaces) ?? []

  /** The page the pager is on, from the page list rather than from a build (§3.1 U-23). */
  const selectedPage = () => {
    const id = attempt(() => model()?.selectedPageID)
    return savedPages().find((page) => attempt(() => WeatherPage.id(page)) === id) ?? null
  }
  const savedPages = () => attempt(() => model()?.pages) ?? []
  const selectedSnapshot = () => screenFor(app, attempt(() => model()?.selectedPageID))?.snapshot ?? null

  /** The device's own fix, whichever place the screen is showing. */
  function phoneCoordinate() {
    const place = selectedSnapshot()?.place ?? null
    if (place != null && place.kind !== 'searched') return place.coordinate
    return attempt(() => model()?.latestSample?.coordinate) ?? null
  }

  function origin() {
    const phone = phoneCoordinate()
    if (phone != null) return phone
    const place = selectedSnapshot()?.place ?? null
    if (place != null) return place.coordinate
    const saved = attempt(() => WeatherPage.savedPlace(selectedPage()))
    return saved == null ? null : attempt(() => WeatherSavedPlace.coordinate(saved))
  }

  /** The page the distances fall back to. Null whenever they really are the user's own. */
  function borrowedOriginName() {
    if (phoneCoordinate() != null) return null
    const place = selectedSnapshot()?.place ?? null
    if (place != null) return placeName(place.label)
    const label = attempt(() => WeatherPage.savedPlace(selectedPage())?.label)
    return label == null ? null : placeName(label)
  }

  /**
   * How far a row is — and, with nothing to measure from at all, that it is not known. A blank
   * line was the worse answer: distance is the only thing telling one "San Juan, PR" from the
   * next (§3.1 U-23).
   */
  function distance({ lat, lon }) {
    const from = origin()
    if (from == null) return t('weather.picker.distanceUnknown')
    const km = attempt(() => MeshWXGeo.distanceKilometres({
      fromLat: from.latitude, fromLon: from.longitude, toLat: lat, toLon: lon,
    }))
    return km == null ? t('weather.picker.distanceUnknown') : kilometres(km)
  }

  /** "86° Cloudy" by the page's own rule, "—" when nothing good is held (§3.1 U-2). */
  function heldReading(coordinate) {
    const reading = attempt(() => WeatherPlaceRowReading.make({
      readings: selectedSnapshot()?.readings ?? [],
      at: coordinate,
      now: now(),
    }))
    return reading == null ? t('weather.picker.noReading') : placeRowText(reading, { now: now(), locale: app?.locale })
  }

  // MARK: - Actions

  function close() {
    handle?.close?.()
  }

  /**
   * A change made in the sheet, redrawn in the sheet. The model's own notification redraws it too,
   * through `Navigation.refresh`, but that is a frame away and coalesced: a bell, a removal and a
   * reorder are direct manipulation and have to answer at once.
   */
  function mutate(fn) {
    attempt(fn)
    refresh()
  }

  function pick(saved) {
    attempt(() => model()?.pick?.(saved))
    close()
  }

  /**
   * My location. **This is one of the two taps that may ask the browser for location** (§5), and
   * it is made after the sheet is gone so a permission prompt never lands on a dismissing sheet.
   */
  function pickMyLocation() {
    close()
    afterNavigationSettles(() => {
      attempt(() => model()?.showPage?.(WeatherPage.myLocationID))
      attempt(() => model()?.useMyLocation?.())
    })
  }

  /** An airport code opens that station's screen and nothing else (§3.1 U-3). */
  function pickStation(station) {
    const index = attempt(() => tables()?.stationIndex({ forICAO: station.icao }))
    if (index == null) return
    const page = screenFor(app, attempt(() => model()?.selectedPageID)) ?? attempt(() => model()?.selectedPageID)
    close()
    afterNavigationSettles(async () => {
      const built = await attemptAsync(async () => {
        const { WeatherStationScreen } = await import('./WeatherStationsView.js')
        return WeatherStationScreen({ app, page, index })
      })
      if (built != null) app?.nav?.push?.(built)
    })
  }

  function setQuery(value) {
    state.query = value
    clearTimeout(state.timer)
    // 35,000 names per keystroke, and the first ZIP query reads the ZIP table.
    state.timer = setTimeout(() => { state.applied = state.query.trim(); refresh() }, 120)
  }

  // MARK: - Sections

  function searchField() {
    // No leading glyph: this icon set draws no magnifier, and a wrong one is worse than none.
    return h('div', { class: 'search' },
      h('input', {
        type: 'search',
        key: 'places-search',
        'data-static': '',
        enterkeyhint: 'search',
        autocomplete: 'off',
        'aria-label': t('weather.picker.prompt'),
        placeholder: t('weather.picker.prompt'),
        hook: (element) => {
          state.input = element
          const onInput = () => setQuery(element.value)
          element.addEventListener('input', onInput)
          return () => { element.removeEventListener('input', onInput); state.input = null }
        },
      }),
      state.applied !== '' || state.query !== ''
        ? h('button', {
          class: 'search__clear',
          type: 'button',
          'aria-label': t('web.place.clearSearch'),
          onclick: () => {
            if (state.input != null) state.input.value = ''
            clearTimeout(state.timer)
            state.query = ''
            state.applied = ''
            refresh()
          },
        }, h('span', { 'aria-hidden': 'true' }, '×'))
        : null)
  }

  function currentLocationSection() {
    const isSelected = attempt(() => model()?.selectedPageID) === WeatherPage.myLocationID
    const coordinate = phoneCoordinate()
    const isWatched = attempt(() => model()?.isMyLocationWatched) === true
    return Card({},
      h('div', { class: 'place-row' },
        h('button', { class: 'place-row__main', type: 'button', onclick: pickMyLocation },
          h('span', { class: 'row__icon' }, icon('location.fill')),
          h('span', { class: 'row__main' },
            h('span', { class: 'row__title' }, t('weather.picker.yourLocation')),
            h('span', { class: 'row__subtitle' }, currentLocationDetail())),
          coordinate != null ? h('span', { class: 'row__value' }, heldReading(coordinate)) : null,
          isSelected
            ? h('span', { class: 'place-row__tick', 'aria-label': t('weather.common.selected') }, icon('checkmark.circle.fill', { size: 18 }))
            : null),
        bell({
          isOn: isWatched,
          placeName: t('weather.notifications.myLocation'),
          onToggle: () => mutate(() => model()?.setMyLocationWatch?.(!isWatched)),
        })))
  }

  function currentLocationDetail() {
    const placeState = attempt(() => model()?.placeState)
    if (placeState === 'denied') return t('weather.picker.locationOff')
    if (placeState === 'needsPermission') return t('weather.picker.allowLocation')
    const place = selectedSnapshot()?.place ?? null
    if (place != null && place.kind !== 'searched') return place.label
    const label = attempt(() => model()?.currentLocationLabel)
    if (label != null) return label
    if (placeState === 'locating') return t('weather.header.locating')
    return t('weather.picker.noFix')
  }

  /**
   * A bell turned on while the browser has notifications blocked would never ring, so it is not
   * turned on: the sheet says so instead (§16).
   */
  function deniedSection() {
    if (attempt(() => model()?.showsNotificationsDenied) !== true) return null
    return Card({},
      h('p', { class: 'card__note' }, t('web.place.notificationsDenied')),
      h('p', { class: 'card__note footnote' }, t('web.place.notificationsDeniedHint')))
  }

  /** The bell beside a place: on means a warning covering it raises a notification (§16.2). */
  function bell({ isOn, placeName: name, onToggle }) {
    return h('button', {
      class: ['place-row__bell', isOn && 'is-on'],
      type: 'button',
      'aria-pressed': isOn ? 'true' : 'false',
      'aria-label': isOn
        ? t('weather.notifications.stopWatching', name)
        : t('weather.notifications.startWatching', name),
      onclick: onToggle,
    }, icon(isOn ? 'bell.fill' : 'bell.slash', { size: 20 }))
  }

  /**
   * The saved places, **in the order the pager swipes through them** (§5). A drag rewrites that
   * order and nothing else moves on its own; the move buttons are the same edit for a keyboard.
   */
  function savedSection() {
    const saved = savedPlaces()
    if (saved.length === 0) return null
    const borrowed = borrowedOriginName()
    const rows = saved.map((place, index) => savedRow({ place, index, count: saved.length }))
    return Card({
      label: t('weather.picker.saved'),
      foot: borrowed == null ? null : t('weather.picker.distancesFrom', borrowed),
    }, rows)
  }

  function savedRow({ place, index, count }) {
    const id = attempt(() => WeatherSavedPlace.id(place))
    const coordinate = attempt(() => WeatherSavedPlace.coordinate(place))
    const isSelected = attempt(() => model()?.selectedPageID) === id
    const move = (toOffset) => mutate(() => model()?.moveSavedPlaces?.({ fromOffsets: [index], toOffset }))

    return h('div', {
      class: ['place-row', state.isEditing && 'place-row--editing'],
      key: id ?? String(index),
      draggable: state.isEditing ? 'true' : null,
      ondragstart: state.isEditing ? (event) => { state.dragID = id; event.dataTransfer.effectAllowed = 'move' } : null,
      ondragover: state.isEditing ? (event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' } : null,
      ondrop: state.isEditing
        ? (event) => {
          event.preventDefault()
          const from = savedPlaces().findIndex((one) => attempt(() => WeatherSavedPlace.id(one)) === state.dragID)
          state.dragID = null
          if (from < 0 || from === index) return
          // SwiftUI's `onMove` convention: the destination is an index into the list *before* the
          // row is taken out, so moving down is one past the target.
          move(from < index ? index + 1 : index)
        }
        : null,
    },
      state.isEditing
        ? h('span', { class: 'place-row__grip', 'aria-hidden': 'true' }, icon('line.3.horizontal', { size: 18 }))
        : null,
      h('button', { class: 'place-row__main', type: 'button', onclick: () => pick(place) },
        h('span', { class: 'row__main' },
          h('span', { class: 'row__title' }, placeName(place.label)),
          h('span', { class: 'row__subtitle' }, coordinate == null ? '' : distance({ lat: place.latitude, lon: place.longitude }))),
        coordinate != null ? h('span', { class: 'row__value' }, heldReading(coordinate)) : null,
        isSelected
          ? h('span', { class: 'place-row__tick', 'aria-label': t('weather.common.selected') }, icon('checkmark.circle.fill', { size: 18 }))
          : null),
      state.isEditing
        ? h('span', { class: 'place-row__edits' },
          h('button', {
            class: 'place-row__move', type: 'button', disabled: index === 0,
            'aria-label': t('web.place.moveUp', placeName(place.label)),
            onclick: () => move(index - 1),
          }, icon('chevron.down', { size: 16, className: 'icon--flip' })),
          h('button', {
            class: 'place-row__move', type: 'button', disabled: index >= count - 1,
            'aria-label': t('web.place.moveDown', placeName(place.label)),
            onclick: () => move(index + 2),
          }, icon('chevron.down', { size: 16 })),
          h('button', {
            class: 'place-row__remove', type: 'button',
            'aria-label': t('web.place.removePlace', placeName(place.label)),
            onclick: () => mutate(() => model()?.removeSavedPlace?.({ id })),
          }, h('span', null, t('weather.picker.remove'))))
        : bell({
          isOn: place.isWatched === true,
          placeName: place.label,
          onToggle: () => mutate(() => model()?.setWatch?.(!place.isWatched, { forPlaceID: id })),
        }))
  }

  /**
   * Forecasts the channel carried that this device did not ask for. Worded as what was heard,
   * never as who asked: the phone does not record that and cannot know it. "This phone can't tell
   * who asked" is **not** repeated here — it is said once, on the radio page (§3.1 U-10).
   */
  function heardSection() {
    const others = selectedSnapshot()?.otherPlaces ?? []
    if (others.length === 0) return null
    return Card({ label: t('weather.picker.heardOnChannel') },
      others.map((other) => Row({
        key: String(other.point?.index ?? ''),
        title: attempt(() => WeatherNames.pointLabel(other.point?.name)) ?? '',
        subtitle: heardDetail(other),
        onclick: () => pick(savedForPoint(other.point)),
      })))
  }

  /** "heard 8 min ago", only for a forecast heard live (a drained backlog is not recent). */
  function heardDetail(other) {
    const parts = [distance({ lat: other.point?.lat, lon: other.point?.lon })]
    const live = attempt(() => model()?.liveHeardAt)
    if (live != null && other.receivedAt <= live) {
      const when = ago(other.receivedAt, { now: now() })
      if (when) parts.push(t('weather.picker.heard', when))
    }
    return parts.filter(Boolean).join(' · ')
  }

  // MARK: - Search

  function results() {
    const text = state.applied
    const table = tables()
    if (text === '' || table == null) return null
    const from = origin()
    const code = attempt(() => MeshWXTables.zipCode({ in: text }))
    if (code != null) {
      const zip = attempt(() => table.zip(text))
      return { kind: 'zip', code, zip }
    }
    const places = attempt(() => WeatherPlaceSearch.ordered(
      table.searchPlaces({ query: text, nearLat: from?.latitude ?? null, lon: from?.longitude ?? null, limit: 25 }),
      { hasOrigin: from != null },
    )) ?? []
    return { kind: 'places', places, stations: stationsMatching(text, from, table) }
  }

  /**
   * Weather stations whose airport code starts with the query, nearest first. Codes only: a name
   * match would bury the towns under every "Municipal Airport".
   */
  function stationsMatching(text, from, table) {
    if (!looksLikeStationCode(text)) return []
    const code = text.toUpperCase()
    const found = attempt(() => table.searchStations({
      query: text, nearLat: from?.latitude ?? null, lon: from?.longitude ?? null, limit: 25,
    })) ?? []
    return found.filter((station) => String(station.icao ?? '').toUpperCase().startsWith(code)).slice(0, 5)
  }

  function searchSection() {
    const found = results()
    if (found == null) return null
    if (found.kind === 'zip') {
      if (found.zip == null) {
        return Card({},
          h('p', { class: 'card__note' }, t('weather.picker.unknownZip', found.code)),
          h('p', { class: 'card__note footnote' }, t('weather.picker.unknownZipHint')))
      }
      return Card({},
        Row({
          title: attempt(() => MeshWXZip.label(found.zip)) ?? found.code,
          subtitle: distance({ lat: found.zip.lat, lon: found.zip.lon }),
          onclick: () => pick(savedForZip(found.zip)),
        }))
    }

    const townCard = found.places.length === 0 && found.stations.length === 0
      ? Card({}, h('p', { class: 'card__note' }, t('weather.picker.noResults')))
      : found.places.length === 0
        ? null
        : Card({}, found.places.map((place) => Row({
          key: `${place.name}-${place.state}-${place.lat}`,
          title: attempt(() => WeatherNames.placeLabel({ name: place.name, state: place.state })) ?? place.name,
          subtitle: distance({ lat: place.lat, lon: place.lon }),
          onclick: () => pick(savedForPlace(place)),
        })))

    const stationCard = found.stations.length === 0
      ? null
      : Card({ label: t('weather.stations.title') }, found.stations.map((station) => Row({
        key: station.icao,
        title: attempt(() => WeatherNames.stationName(station.name)) ?? station.name,
        subtitle: [station.icao, distance({ lat: station.lat, lon: station.lon })].filter(Boolean).join(' · '),
        onclick: () => pickStation(station),
      })))

    return [townCard, stationCard]
  }

  // MARK: - Presenting

  handle = app?.nav?.sheet?.({
    title: () => t('weather.place.places'),
    done: t('weather.common.done'),
    leading: () => (savedPlaces().length === 0
      ? null
      : Button({
        label: state.isEditing ? t('web.place.editDone') : t('web.place.edit'),
        kind: 'plain',
        onclick: () => { state.isEditing = !state.isEditing; refresh() },
      })),
    render: () => h('div', { class: 'list' },
      searchField(),
      state.applied === ''
        ? [currentLocationSection(), deniedSection(), savedSection(), heardSection()]
        : searchSection()),
    onDismiss: () => { state.isEditing = false; clearTimeout(state.timer) },
  })
  return handle
}

/** "KAUS", "TJSJ", "7R5": three or four letters and digits, what an airport code looks like. */
export function looksLikeStationCode(text) {
  return /^[A-Za-z0-9]{3,4}$/.test(text)
}

function savedForPlace(place) {
  return attempt(() => WeatherSavedPlace.from(WeatherPlace.searched(place), { at: Date.now() }))
}

function savedForZip(zip) {
  return attempt(() => WeatherSavedPlace.from(WeatherPlace.zip(zip), { at: Date.now() }))
}

/** A forecast point held from the channel, as a searched place. */
function savedForPoint(point) {
  return attempt(() => WeatherSavedPlace.from(
    WeatherPlace.make({
      kind: 'searched',
      coordinate: { latitude: point.lat, longitude: point.lon },
      label: WeatherNames.pointLabel(point.name),
      uncertaintyKilometres: WeatherPlace.searchedUncertaintyKilometres,
      searchedAs: 'forecastPoint',
    }),
    { at: Date.now() },
  ))
}

async function attemptAsync(fn) {
  try {
    return await fn()
  } catch (error) {
    if (typeof console !== 'undefined') console.warn('[place] could not open the screen', error)
    return null
  }
}
