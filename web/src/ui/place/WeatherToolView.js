// Port of MC1/Views/Tools/Weather/WeatherToolView.swift
//
// The Weather tool's root screen: **a pager of places**, swiped sideways with dots, My location
// first (docs/MESHWX_UI.md §4, §3.2 Q2, Q12).
//
// The chrome is the shell's own (§3.1.2 V-2, V-4). The place's name is the title, and the title is
// a menu: tapping it lists the pages, the one on screen ticked, and ends in "Add or edit places…",
// which opens Places — the answer to "how do I change the place" that a bare list glyph in a
// corner never was. **Places · the page dots · Update** live in the bottom bar, in the same three
// positions on every page, and My location's dot is the location arrow, as it is in Apple Weather.
// Both bar controls are words, not glyphs.
//
// There is no pull-to-refresh on the web and none is wanted: Update is the refresh, and it says
// what it will spend before it is tapped (§11.1, and §3.1 O-4, which preferred the button for
// exactly that reason).

import { h } from '../kit/dom.js'
import { icon } from '../kit/icons.js'
import { Button, openMenu } from '../kit/components.js'
import { Pager, PageDots, scrollPagerTo } from '../kit/pager.js'
import { t } from '../../l10n.js'
import { WeatherPage, WeatherPages, WeatherUpdatePlan } from '../../screen/index.js'
import { WeatherPlacePageView } from './WeatherPlacePageView.js'
import { WeatherUpdateControl } from './WeatherUpdateControl.js'
import { WeatherPendingBar, requestKeys } from './WeatherAskControl.js'
import { openPlaces } from './WeatherPlacePickerView.js'
import { attempt, loadAppNamespace, placeName, planOf, screenFor, screenValue } from './support.js'

/**
 * The tool's root screen.
 *
 * @param {object} options
 * @param {object} options.app  `{ nav, model, tables, geometry, basemap, timeZone, locale,
 *   openConnect?, radioPill? }`
 */
export function WeatherToolScreen({ app }) {
  const state = {
    track: null,
    /** The page the pager was last put on, so a swipe is never fought and a pick always lands. */
    settledID: null,
    pageCount: -1,
    /** Places opens itself once, on a first visit with no permission and no saved place (§5). */
    hasOfferedPlaces: false,
  }

  const refresh = () => app?.nav?.refresh?.()
  // `WeatherCopy` and `WeatherFormatting` live in `src/app/`, which may land after these screens:
  // the page renders without them and fills in when they arrive.
  loadAppNamespace(refresh)
  attempt(() => app?.model?.subscribe?.(() => { offerPlacesOnFirstOpen(); refresh() }))

  function pages() {
    return attempt(() => app?.model?.pages) ?? []
  }

  function selectedID() {
    const held = attempt(() => app?.model?.selectedPageID) ?? WeatherPage.myLocationID
    return attempt(() => WeatherPages.selection(held, { in: pages() })) ?? held
  }

  function selectedScreen() {
    return screenFor(app, selectedID())
  }

  /**
   * The place on screen names the screen, by the one label function every other screen uses
   * (§3.1 U-12). The pages carry no headline of their own.
   */
  function titleText() {
    const page = pages().find((one) => attempt(() => WeatherPage.id(one)) === selectedID())
    if (page == null) return t('weather.title')
    const label = attempt(() => WeatherPage.label(page))
    if (label != null) return placeName(label)
    const place = selectedScreen()?.snapshot?.place ?? null
    return place == null ? t('weather.picker.yourLocation') : placeName(place.label)
  }

  function useMyLocation() {
    attempt(() => app?.model?.useMyLocation?.())
  }

  function showPlaces() {
    openPlaces({ app })
  }

  /**
   * Opened before location permission was ever granted: the list is where a place comes from, and
   * **nothing is asked of the browser** until the user taps My location there or on the page (§5).
   */
  function offerPlacesOnFirstOpen() {
    if (state.hasOfferedPlaces) return
    const model = app?.model
    if (model == null) return
    const saved = attempt(() => model.savedPlaces) ?? []
    const placeState = attempt(() => model.placeState)
    if (placeState !== 'needsPermission' || saved.length > 0) return
    state.hasOfferedPlaces = true
    showPlaces()
  }

  /** The title's menu: every page, the one on screen ticked, and the way into Places. */
  function titleMenu(anchor) {
    const current = selectedID()
    const items = pages().map((page) => {
      const id = attempt(() => WeatherPage.id(page))
      const label = attempt(() => WeatherPage.label(page))
      return {
        label: id === WeatherPage.myLocationID ? t('weather.picker.yourLocation') : placeName(label ?? ''),
        icon: id === WeatherPage.myLocationID ? 'location.fill' : null,
        checked: id === current,
        onSelect: () => attempt(() => app?.model?.showPage?.(id)),
      }
    })
    items.push('divider')
    items.push({ label: t('weather.place.manage'), icon: 'list.bullet', onSelect: showPlaces })
    // The alert map is one tap from anywhere in the tool, not three down the radio page
    // (docs/MESHWX_UI.md §3.1 U-32). This icon set draws no map glyph, so the row is its words.
    items.push({
      label: t('weather.areaMap.title'),
      onSelect: async () => {
        const screen = selectedScreen() ?? selectedID()
        const built = await attemptAsync(async () => {
          const { WeatherAreaMapScreen } = await import('../radio/index.js')
          return WeatherAreaMapScreen({ app, page: screen })
        })
        if (built != null) app?.nav?.push?.(built)
      },
    })
    openMenu(anchor, items)
  }

  function renderPager() {
    const list = pages()
    const current = selectedID()
    const track = Pager({
      pages: list.map((page) => ({
        id: attempt(() => WeatherPage.id(page)) ?? WeatherPage.myLocationID,
        render: () => WeatherPlacePageView({ app, page, onUseMyLocation: useMyLocation }),
      })),
      selectedID: current,
      onSettle: (id) => {
        state.settledID = id
        attempt(() => app?.model?.showPage?.(id))
      },
    })
    // The root is a pager, so its screen scrolls sideways and not down: `.screen--pager` is the
    // kit's own class for that, and only the element itself can reach the section nav made.
    track.dataset.hook = ''
    track.__hook = (element) => {
      state.track = element
      element.closest('.screen')?.classList.add('screen--pager')
    }

    // A pick from the menu, the dots or Places moves the pager; a swipe already moved it, and is
    // never fought.
    if (state.settledID !== current || state.pageCount !== list.length) {
      state.settledID = current
      state.pageCount = list.length
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => attempt(() => scrollPagerTo(state.track, current)))
      }
    }
    return track
  }

  function renderToolbar() {
    const screen = selectedScreen()
    const plan = planOf(screen)
    const list = pages()
    const current = selectedID()
    const dots = list.map((page) => {
      const id = attempt(() => WeatherPage.id(page)) ?? WeatherPage.myLocationID
      const label = attempt(() => WeatherPage.label(page))
      return {
        id,
        label: id === WeatherPage.myLocationID ? t('weather.picker.yourLocation') : placeName(label ?? ''),
      }
    })

    return h('div', { class: 'toolbar__inner' },
      Button({
        label: t('weather.place.places'),
        kind: 'plain',
        strong: true,
        onclick: showPlaces,
      }),
      PageDots({
        pages: dots,
        selectedID: current,
        onSelect: (id) => attempt(() => app?.model?.showPage?.(id)),
        iconFor: (page) => (page.id === WeatherPage.myLocationID ? icon('location.fill', { size: 12 }) : null),
      }),
      WeatherUpdateControl({ app, screen, plan }))
  }

  /** What Update on this screen speaks for. The bar is for a request neither it nor a page shows. */
  function requestsOnScreen() {
    const screen = selectedScreen()
    if (screen == null) return new Set()
    const planned = attempt(() => WeatherUpdatePlan.requests(planOf(screen))) ?? []
    const sent = screenValue(screen, 'updateRequests', (one) => one.model?.updateRequests?.({ pageID: one.pageID })) ?? []
    return requestKeys([...planned, ...sent])
  }

  return {
    id: 'root',
    title: () => h('button', {
      class: 'title-menu',
      type: 'button',
      'aria-haspopup': 'menu',
      onclick: (event) => titleMenu(event.currentTarget),
    }, h('span', null, titleText()), icon('chevron.down', { size: 16 })),
    render: () => h('div', { class: 'pager-host' },
      renderPager(),
      // A request on the air whose button is not on this screen, over the foot of the page rather
      // than in a row that scrolls away — an overlay, so the list never jumps for it (§3.1 U-33).
      WeatherPendingBar({ app, requestsOnScreen: requestsOnScreen() })),
    trailing: () => attempt(() => app?.radioPill?.()) ?? null,
    toolbar: () => renderToolbar(),
    onAppear() {
      offerPlacesOnFirstOpen()
    },
  }
}

async function attemptAsync(fn) {
  try {
    return await fn()
  } catch (error) {
    if (typeof console !== 'undefined') console.warn('[place] could not open the screen', error)
    return null
  }
}
