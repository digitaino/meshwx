// Navigation, the way the iOS tool does it: one root screen, detail screens that **push** over
// it, and sheets for the screens that are about the tool rather than about a place
// (docs/MESHWX_UI.md §4). Pushes are browser history entries, so the back button and Android's
// back gesture pop them.
//
// A screen is `{ id, title, render, toolbar?, trailing?, fullBleed?, onAppear?, onDisappear? }`:
// - `title()`    → string or Node for the bar
// - `render()`   → Node, the screen's body; called again on every `nav.refresh()`
// - `toolbar()`  → Node or null, the bottom bar (the root's Places · dots · Update)
// - `trailing()` → Node or null, the bar's trailing item
import { h, morph, clear, runHooks, dispose } from './dom.js'
import { icon } from './icons.js'

export class Navigation {
  constructor(container, { backLabel = 'Back' } = {}) {
    this.container = container
    this.backLabel = backLabel
    this.stack = []
    this.sheetStack = []
    this.scheduled = false

    this.bar = h('header', { class: 'navbar' })
    this.body = h('main', { class: 'screens' })
    this.foot = h('footer', { class: 'toolbar', hidden: true })
    clear(container)
    container.append(this.bar, this.body, this.foot)

    window.addEventListener('popstate', (event) => this.#onPopState(event))
  }

  get top() { return this.stack[this.stack.length - 1] ?? null }

  setRoot(screen) {
    for (const entry of this.stack) this.#teardown(entry)
    this.stack = []
    history.replaceState({ depth: 0 }, '')
    this.#present(screen)
  }

  push(screen) {
    history.pushState({ depth: this.stack.length }, '')
    this.#present(screen)
  }

  pop() {
    if (this.stack.length > 1) history.back()
  }

  popToRoot() {
    const extra = this.stack.length - 1
    if (extra > 0) history.go(-extra)
  }

  /** Re-renders what is on screen. Coalesced to one pass per frame. */
  refresh() {
    if (this.scheduled) return
    this.scheduled = true
    // A hidden tab never gets its animation frame, and a latch that waits for one would never
    // refresh again: the timer is the fallback, and whichever comes first does the work.
    const run = () => {
      if (!this.scheduled) return
      this.scheduled = false
      clearTimeout(timer)
      this.#render()
      for (const sheet of this.sheetStack) sheet.refresh()
    }
    const timer = setTimeout(run, 250)
    requestAnimationFrame(run)
  }

  #present(screen) {
    const previous = this.top
    previous?.screen.onDisappear?.()
    if (previous) previous.element.hidden = true
    const element = h('section', { class: ['screen', screen.fullBleed && 'screen--bleed'], dataset: { screen: screen.id ?? '' } })
    this.body.append(element)
    this.stack.push({ screen, element, root: null })
    this.#render()
    screen.onAppear?.()
    element.scrollTop = 0
  }

  #onPopState(event) {
    if (this.sheetStack.length) {
      // A sheet swallowed this back: close it and stay where we are.
      this.sheetStack[this.sheetStack.length - 1].close({ fromHistory: true })
      return
    }
    const depth = event.state?.depth ?? 0
    while (this.stack.length - 1 > depth) {
      const entry = this.stack.pop()
      this.#teardown(entry)
    }
    const top = this.top
    if (top) {
      top.element.hidden = false
      top.screen.onAppear?.()
    }
    this.#render()
  }

  #teardown(entry) {
    entry.screen.onDisappear?.()
    entry.screen.onRemove?.()
    dispose(entry.element)
    entry.element.remove()
  }

  #render() {
    const entry = this.top
    if (!entry) return
    const { screen } = entry

    const next = screen.render()
    if (entry.root && entry.root.isConnected) entry.root = morph(entry.root, next)
    else { clear(entry.element); entry.root = next; entry.element.append(next) }
    runHooks(entry.element)

    const title = screen.title?.() ?? ''
    const back = this.stack.length > 1
      ? h('button', { class: 'navbar__back', type: 'button', 'aria-label': this.backLabel, onclick: () => this.pop() },
          icon('chevron.left'), h('span', null, this.backLabel))
      : h('span', { class: 'navbar__back navbar__back--none' })
    const nextBar = h('header', { class: 'navbar' },
      back,
      h('div', { class: 'navbar__title' }, typeof title === 'string' ? h('h1', null, title) : title),
      h('div', { class: 'navbar__trailing' }, screen.trailing?.() ?? null))
    this.bar = morph(this.bar, nextBar)
    document.title = typeof title === 'string' && title ? `${title} · MeshWX` : 'MeshWX'

    const tools = screen.toolbar?.() ?? null
    const nextFoot = h('footer', { class: 'toolbar', hidden: tools == null }, tools)
    this.foot = morph(this.foot, nextFoot)
    this.container.classList.toggle('has-toolbar', tools != null)
  }

  // MARK: Sheets

  /**
   * Presents `{ title, render, onDismiss?, done? }` as a modal sheet. Returns a handle with
   * `close()` and `refresh()`. A sheet is a history entry too, so back closes it.
   */
  sheet(spec) {
    const dialog = h('dialog', { class: 'sheet' })
    const handle = {
      spec, dialog, root: null, closed: false,
      refresh: () => {
        if (handle.closed) return
        const next = h('div', { class: 'sheet__panel' },
          h('header', { class: 'sheet__bar' },
            h('div', { class: 'sheet__leading' }, spec.leading?.() ?? null),
            h('h2', null, spec.title?.() ?? ''),
            h('div', { class: 'sheet__trailing' },
              h('button', { class: 'button button--plain button--strong', type: 'button', onclick: () => handle.close() }, spec.done ?? 'Done'))),
          h('div', { class: 'sheet__body' }, spec.render(handle)))
        if (handle.root && handle.root.isConnected) handle.root = morph(handle.root, next)
        else { clear(dialog); handle.root = next; dialog.append(next) }
        runHooks(dialog)
      },
      close: ({ fromHistory = false } = {}) => {
        if (handle.closed) return
        handle.closed = true
        this.sheetStack = this.sheetStack.filter((s) => s !== handle)
        dialog.close()
        dispose(dialog)
        dialog.remove()
        if (!fromHistory) history.back()
        spec.onDismiss?.()
      },
    }
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); handle.close() })
    dialog.addEventListener('click', (event) => { if (event.target === dialog) handle.close() })
    document.body.append(dialog)
    this.sheetStack.push(handle)
    history.pushState({ depth: this.stack.length - 1, sheet: true }, '')
    handle.refresh()
    dialog.showModal()
    return handle
  }
}
