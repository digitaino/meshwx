// The whole of the UI framework: build DOM with `h`, and bring a live tree up to date with
// `morph` so a re-render keeps scroll position, focus and running transitions.

const SVG_NS = 'http://www.w3.org/2000/svg'
const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'g', 'ellipse', 'defs', 'use', 'title'])

/**
 * `h('div', { class: 'row', onclick }, child, 'text', [more])`.
 * Props: `class`, `style` (string or object), `dataset`, `on<event>` handlers, `key`, `hook`, boolean
 * and string attributes. `null`, `false` and `undefined` children are skipped.
 *
 * `hook: (el) => cleanup` runs once, after the element is in the document (`runHooks`), and its
 * cleanup runs when `morph` removes it. With `data-static` it is how a screen mounts something
 * that must survive re-renders: a canvas map, a text field.
 */
export function h(tag, props, ...children) {
  const el = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag)
  if (props) {
    for (const [name, value] of Object.entries(props)) {
      if (value == null || value === false) continue
      if (name === 'class') el.setAttribute('class', Array.isArray(value) ? value.filter(Boolean).join(' ') : value)
      else if (name === 'style' && typeof value === 'object') Object.assign(el.style, value)
      else if (name === 'dataset') Object.assign(el.dataset, value)
      else if (name === 'key') el.dataset.key = String(value)
      else if (name === 'hook') { el.__hook = value; el.dataset.hook = '' }
      else if (name.startsWith('on') && typeof value === 'function') {
        el.addEventListener(name.slice(2).toLowerCase(), value)
        ;(el.__handlers ??= {})[name] = value
      } else if (name === 'html') el.innerHTML = value
      else if (value === true) el.setAttribute(name, '')
      else el.setAttribute(name, String(value))
    }
  }
  append(el, children)
  return el
}

function append(el, children) {
  for (const child of children) {
    if (child == null || child === false || child === true) continue
    if (Array.isArray(child)) append(el, child)
    else el.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild)
}

/**
 * Makes `live` look like `next`, touching only what differs. Children are matched by
 * `data-key` when they have one, otherwise by position and tag. Event handlers registered through
 * `h` are swapped over, so closures in a fresh render see fresh state. An element marked
 * `data-static` is left alone once mounted (a canvas map, an input being typed in).
 */
export function morph(live, next) {
  if (live.nodeType !== next.nodeType || live.nodeName !== next.nodeName) {
    dispose(live)
    live.replaceWith(next)
    return next
  }
  if (live.nodeType === Node.TEXT_NODE) {
    if (live.nodeValue !== next.nodeValue) live.nodeValue = next.nodeValue
    return live
  }
  if (live.nodeType !== Node.ELEMENT_NODE) return live
  if (live.dataset?.static != null && next.dataset?.static != null) return live

  for (const { name } of [...live.attributes]) if (!next.hasAttribute(name)) live.removeAttribute(name)
  for (const { name, value } of [...next.attributes]) if (live.getAttribute(name) !== value) live.setAttribute(name, value)
  if ('value' in live && live.tagName !== 'BUTTON' && document.activeElement !== live && live.value !== next.value) live.value = next.value
  if ('checked' in live) live.checked = next.checked

  const before = live.__handlers ?? {}
  const after = next.__handlers ?? {}
  for (const [name, fn] of Object.entries(before)) if (after[name] !== fn) live.removeEventListener(name.slice(2).toLowerCase(), fn)
  for (const [name, fn] of Object.entries(after)) if (before[name] !== fn) live.addEventListener(name.slice(2).toLowerCase(), fn)
  live.__handlers = after

  const keyed = new Map()
  for (const child of live.children) if (child.dataset?.key != null) keyed.set(child.dataset.key, child)

  let cursor = live.firstChild
  for (const wanted of [...next.childNodes]) {
    const key = wanted.nodeType === Node.ELEMENT_NODE ? wanted.dataset?.key : null
    let match = null
    if (key != null) match = keyed.get(key) ?? null
    else if (cursor && cursor.nodeType === wanted.nodeType && cursor.nodeName === wanted.nodeName &&
      !(cursor.nodeType === Node.ELEMENT_NODE && cursor.dataset?.key != null)) match = cursor

    if (match) {
      if (match !== cursor) live.insertBefore(match, cursor)
      else cursor = cursor.nextSibling
      const result = morph(match, wanted)
      if (result !== match && cursor === match) cursor = result.nextSibling
      if (key != null) keyed.delete(key)
    } else {
      live.insertBefore(wanted, cursor)
    }
  }
  while (cursor) {
    const gone = cursor
    cursor = cursor.nextSibling
    dispose(gone)
    gone.remove()
  }
  return live
}

/** Runs the `hook` of every element under `root` that has not had it run yet. */
export function runHooks(root) {
  if (!root?.querySelectorAll) return
  const pending = root.matches?.('[data-hook]') ? [root, ...root.querySelectorAll('[data-hook]')] : root.querySelectorAll('[data-hook]')
  for (const el of pending) {
    if (el.__hooked || typeof el.__hook !== 'function') continue
    el.__hooked = true
    el.__cleanup = el.__hook(el) ?? null
  }
}

/** Runs the hook cleanups under a node that is leaving the document. */
export function dispose(node) {
  if (node?.nodeType !== Node.ELEMENT_NODE) return
  const hooked = node.matches('[data-hook]') ? [node, ...node.querySelectorAll('[data-hook]')] : node.querySelectorAll('[data-hook]')
  for (const el of hooked) {
    if (typeof el.__cleanup === 'function') el.__cleanup()
    el.__cleanup = null
  }
}

/** Renders `view()` into `container` now and whenever `rerender()` is called. */
export function mountView(container, view) {
  let root = null
  let scheduled = false
  const render = () => {
    scheduled = false
    const next = view()
    if (!root || !root.isConnected) {
      clear(container)
      root = next
      container.append(root)
    } else {
      root = morph(root, next)
    }
    runHooks(root)
  }
  render()
  return {
    rerender() {
      if (scheduled) return
      scheduled = true
      requestAnimationFrame(render)
    },
    get root() { return root },
  }
}
