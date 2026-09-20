// A sideways pager with index dots, built on CSS scroll snapping: the browser does the physics,
// and this only reports which page the scroll settled on (docs/MESHWX_UI.md §4: one page per
// place, My location first).
import { h } from './dom.js'

/**
 * @param {object} options
 * @param {{ id: string, render: () => Node }[]} options.pages
 * @param {string} options.selectedID
 * @param {(id: string) => void} options.onSettle called when a swipe comes to rest on a new page
 */
export function Pager({ pages, selectedID, onSettle }) {
  // The handlers go through `h` so that `morph` swaps them on a re-render: the live element
  // must never keep a closure over an older list of pages.
  let timer = null
  const settle = (event) => {
    const track = event.currentTarget
    const width = track.clientWidth || 1
    const index = Math.round(track.scrollLeft / width)
    const page = pages[Math.max(0, Math.min(pages.length - 1, index))]
    if (page && page.id !== track.dataset.selected) {
      track.dataset.selected = page.id
      onSettle(page.id)
    }
  }
  return h('div', {
    class: 'pager',
    dataset: { pager: '', selected: selectedID },
    onscroll: (event) => {
      clearTimeout(timer)
      const target = event.currentTarget
      timer = setTimeout(() => settle({ currentTarget: target }), 140)
    },
    onscrollend: settle,
  }, pages.map((page) => h('div', { class: 'pager__page', key: page.id, dataset: { page: page.id } }, page.render())))
}

/** Brings the pager to `id` without animation when it is not already there. */
export function scrollPagerTo(track, id, { smooth = false } = {}) {
  const page = track?.querySelector(`:scope > [data-page="${CSS.escape(id)}"]`)
  if (!page) return
  const left = page.offsetLeft
  if (Math.abs(track.scrollLeft - left) < 2) return
  track.dataset.selected = id
  track.scrollTo({ left, behavior: smooth ? 'smooth' : 'instant' })
}

/** The dots: My location's is the location arrow, as in Apple Weather. */
export function PageDots({ pages, selectedID, onSelect, iconFor }) {
  return h('div', { class: 'dots', role: 'tablist' },
    pages.map((page) => h('button', {
      class: ['dots__dot', page.id === selectedID && 'is-selected', iconFor?.(page) && 'dots__dot--icon'],
      type: 'button', role: 'tab', key: page.id,
      'aria-selected': page.id === selectedID ? 'true' : 'false',
      'aria-label': page.label,
      onclick: () => onSelect(page.id),
    }, iconFor?.(page) ?? null)))
}
