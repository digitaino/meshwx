// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherPages.swift

import { WeatherSavedPlace } from './WeatherPlace.js'
import { WeatherWatchedPlace } from './WeatherAlertWatch.js'

const MY_LOCATION_ID = 'here'

/**
 * One page of the Weather tool's pager (docs/MESHWX_UI.md §4).
 *
 * The tool is a weather app: one page per place, swiped sideways with dots, and the phone's own
 * location always first. A page is named by what it *is* — the phone's position, or one saved
 * place — so a page keeps its identity while the list is added to, reordered or thinned.
 */
export const WeatherPage = Object.freeze({
  myLocationID: MY_LOCATION_ID,

  /**
   * Where the phone is. Always the first page, whether or not a fix has been taken: with no
   * permission it is the page that offers to ask for one, and nothing asks for it first.
   */
  myLocation: Object.freeze({ kind: 'myLocation' }),

  saved(place) {
    return { kind: 'saved', value: place }
  },

  id(page) {
    return page.kind === 'myLocation' ? MY_LOCATION_ID : WeatherSavedPlace.id(page.value)
  },

  savedPlace(page) {
    return page.kind === 'myLocation' ? null : page.value
  },

  /**
   * What the page is titled with. My location has no title of its own: its place, once resolved,
   * carries one.
   */
  label(page) {
    return WeatherPage.savedPlace(page)?.label ?? null
  },
})

/** The pages the pager swipes through, in order (docs/MESHWX_UI.md §4). */
export const WeatherPages = Object.freeze({
  /**
   * My location first, then the saved places in the order Places holds them. Places is where the
   * order is decided — adding, removing and dragging — and the pager follows it exactly, so the
   * dots never mean something different from the list.
   */
  make({ saved }) {
    return [WeatherPage.myLocation, ...saved.map((place) => WeatherPage.saved(place))]
  },

  /**
   * The page the pager should be showing: the one asked for while it is still there, else the
   * first. A place removed from Places while its page was open leaves the pager on My location
   * rather than on an id nothing answers to.
   *
   * The caller writes the answer back. Resolving it only where it is read leaves the selection
   * pointing at a page that is gone, which is a snapshot nothing can be built for and a spinner
   * that never ends.
   */
  selection(id, { in: pages }) {
    if (pages.some((page) => WeatherPage.id(page) === id)) return id
    return pages.length > 0 ? WeatherPage.id(pages[0]) : MY_LOCATION_ID
  },

  /** The page on either side of one, for the two neighbours a pager can be swiped to next. */
  neighbours({ of: id, in: pages }) {
    const index = pages.findIndex((page) => WeatherPage.id(page) === id)
    if (index < 0) return []
    return [index - 1, index + 1]
      .filter((position) => position >= 0 && position < pages.length)
      .map((position) => WeatherPage.id(pages[position]))
  },

  /**
   * The page a watched place belongs to. The notifier watches places, not pages, and names the
   * phone's own position `WeatherWatchedPlace.myLocationID`; every other id is a saved place's,
   * which is its page's (docs/MESHWX_UI.md §16).
   */
  pageID({ forWatchedPlaceID: id }) {
    return id === WeatherWatchedPlace.myLocationID ? MY_LOCATION_ID : id
  },
})
