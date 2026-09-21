// The handful of building blocks every screen is made of, so that screens written apart look like
// one tool. They map one to one onto the classes in `styles/app.css`; a screen should rarely need
// a class of its own.
//
// The shape is the iOS tool's: a screen is a column (`List`) of cards, a card is a list of rows
// with its label inside it in small caps (docs/MESHWX_UI.md §3.1.2: "FORECAST · ISSUED 4:02 PM"),
// and a row that goes somewhere is a button with a chevron.
import { h } from './dom.js'
import { icon } from './icons.js'

/** The scrolling column of a screen. */
export function List(...children) {
  return h('div', { class: 'list' }, children)
}

/**
 * A card. `label` is the small-caps line inside its top edge, `labelTrailing` sits at the label's
 * right ("ISSUED 4:02 PM"), `foot` is the quiet last line ("Austin Camp Mabry · 6 km").
 */
export function Card({ label = null, labelTrailing = null, foot = null, key = null, class: extra = null } = {}, ...rows) {
  return h('section', { class: ['card', extra], key },
    label != null || labelTrailing != null
      ? h('div', { class: 'card__label' }, h('span', null, label ?? ''), labelTrailing != null ? h('span', null, labelTrailing) : null)
      : null,
    rows,
    foot != null ? h('div', { class: 'card__foot' }, foot) : null)
}

/** A line of quiet text under a card, outside it (SwiftUI's section footer). */
export function Note(...children) {
  return h('p', { class: 'list__note' }, children)
}

/** A small-caps header above a card, outside it. Prefer `Card({ label })`. */
export function Header(text) {
  return h('h2', { class: 'list__header' }, text)
}

/**
 * One row. With `onclick` it is a button and shows a chevron unless `chevron: false`.
 * `icon` is an icon name or a Node; `value` is the trailing text or Node; `trailing` is any Node
 * placed last (a switch, a small button). `tint` sets `--tint` to an event tint name.
 */
export function Row({
  icon: iconName = null, title = null, subtitle = null, value = null, trailing = null,
  onclick = null, chevron = onclick != null, stale = false, destructive = false, muted = false,
  disabled = false, key = null, tint = null, class: extra = null, label = null,
} = {}) {
  const classes = ['row', stale && 'row--stale', destructive && 'row--destructive', muted && 'row--muted', extra]
  const style = tint ? `--tint: var(--tint-${tint})` : null
  const children = [
    iconName ? h('span', { class: 'row__icon' }, typeof iconName === 'string' ? icon(iconName) : iconName) : null,
    h('span', { class: 'row__main' },
      title != null ? h('span', { class: 'row__title' }, title) : null,
      subtitle != null ? h('span', { class: 'row__subtitle' }, subtitle) : null),
    value != null ? h('span', { class: 'row__value' }, value) : null,
    trailing,
    chevron ? h('span', { class: 'row__chevron' }, icon('chevron.right', { size: 16 })) : null,
  ]
  if (onclick) return h('button', { class: classes, type: 'button', onclick, disabled, key, style, 'aria-label': label }, children)
  return h('div', { class: classes, key, style }, children)
}

/** `kind`: 'default' | 'primary' | 'plain' | 'destructive'. */
export function Button({ label, icon: iconName = null, kind = 'default', small = false, block = false, strong = false, disabled = false, onclick, key = null, ariaLabel = null }) {
  return h('button', {
    class: ['button', kind === 'primary' && 'button--primary', (kind === 'plain' || kind === 'destructive') && 'button--plain',
      kind === 'destructive' && 'button--destructive', small && 'button--small', block && 'button--block', strong && 'button--strong',
      label == null && 'button--icon'],
    type: 'button', disabled, onclick, key, 'aria-label': ariaLabel ?? (label == null ? iconName : null),
  }, iconName ? icon(iconName, { size: small ? 16 : 18 }) : null, label != null ? h('span', null, label) : null)
}

/** An orange (or, with `kind: 'info'`, accent) notice with optional buttons under its text. */
export function Banner({ icon: iconName = 'exclamationmark.triangle', text, detail = null, actions = [], kind = 'warning', key = null }) {
  return h('div', { class: ['banner', kind === 'info' && 'banner--info'], key, role: 'status' },
    icon(iconName),
    h('div', { class: 'banner__main' },
      h('div', null, h('div', null, text), detail ? h('div', { class: 'footnote' }, detail) : null),
      actions.length ? h('div', { class: 'cluster' }, actions) : null))
}

/**
 * A row whose value is a field you type in: the label on the left, the entry on the right, a quiet
 * unit after it ("MHz"). `value` comes from the screen's state on every render, so `morph` keeps it
 * in step without rebuilding the input — and leaves it alone while it has focus.
 *
 * Always `type="text"` with an `inputmode`, never `type="number"`: a number input reports `""`
 * for anything it dislikes, so "910." mid-typing would come back as nothing and the digits already
 * entered would be lost. Parsing and range checking belong to the caller, which is the only place
 * that knows what the field is for (`RadioParameters`).
 */
export function Field({
  label, value = '', unit = null, detail = null, placeholder = null, inputmode = null,
  oninput = null, onchange = null, disabled = false, invalid = false, maxlength = null,
  wide = false, id = null, key = null,
}) {
  const fieldID = id ?? `field-${slug(label)}`
  return h('div', { class: ['row', 'field-row', wide && 'field-row--wide'], key },
    h('label', { class: 'row__main', for: fieldID },
      h('span', { class: 'row__title' }, label),
      detail != null ? h('span', { class: 'row__subtitle' }, detail) : null),
    h('span', { class: ['field-row__entry', invalid && 'is-invalid'] },
      h('input', {
        class: 'field-row__input', id: fieldID, type: 'text', value: String(value ?? ''),
        placeholder, inputmode, maxlength, disabled,
        autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
        'aria-invalid': invalid ? 'true' : null,
        oninput: oninput ? (event) => oninput(event.currentTarget.value) : null,
        onchange: onchange ? (event) => onchange(event.currentTarget.value) : null,
      }),
      unit != null ? h('span', { class: 'field-row__unit' }, unit) : null))
}

/**
 * A row whose value is one of a list. `options` is `[{ value, label, disabled? }]`, or
 * `[{ group, options: [...] }]` for an `optgroup` (the preset picker's regions). `onchange` gets
 * the chosen option's value as a string.
 *
 * Keep the option list stable across renders: `morph` sets the select's value before it updates
 * the options, so a value whose option has just appeared would not take.
 */
export function Select({
  label, value, options, onchange, detail = null, disabled = false, invalid = false,
  id = null, key = null,
}) {
  const fieldID = id ?? `field-${slug(label)}`
  const current = value == null ? '' : String(value)
  const build = (list) => list.map((option) => (option.options
    ? h('optgroup', { label: option.group }, build(option.options))
    : h('option', {
      value: String(option.value),
      selected: String(option.value) === current ? true : null,
      disabled: option.disabled ? true : null,
    }, option.label)))
  return h('div', { class: ['row', 'field-row'], key },
    h('label', { class: 'row__main', for: fieldID },
      h('span', { class: 'row__title' }, label),
      detail != null ? h('span', { class: 'row__subtitle' }, detail) : null),
    h('span', { class: ['field-row__entry', 'field-row__entry--select', invalid && 'is-invalid'] },
      h('select', {
        class: 'field-row__select', id: fieldID, disabled,
        'aria-invalid': invalid ? 'true' : null,
        onchange: (event) => onchange(event.currentTarget.value),
      }, build(options))))
}

function slug(text) {
  return String(text ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}

export function Switch({ checked, onchange, label, id = null, disabled = false }) {
  return h('label', { class: 'switch', 'aria-label': label },
    h('input', { type: 'checkbox', role: 'switch', id, checked: checked ? true : null, disabled, onchange: (event) => onchange(event.currentTarget.checked) }),
    h('span'))
}

export function Spinner() {
  return h('span', { class: 'spinner', role: 'progressbar', 'aria-label': 'Working' })
}

export function Empty(...children) {
  return h('div', { class: 'empty' }, children)
}

/** Free text inside a card: a paragraph or several. */
export function Prose(...paragraphs) {
  return h('div', { class: 'prose' }, paragraphs.map((p) => (typeof p === 'string' ? h('p', null, p) : p)))
}

/**
 * A pop-up menu anchored under `anchor` (the title menu, a row's "…").
 * `items`: `{ label, icon?, checked?, destructive?, onSelect }` or the string `'divider'`.
 */
export function openMenu(anchor, items) {
  closeMenu()
  const rect = anchor.getBoundingClientRect()
  const scrim = h('div', { class: 'menu-scrim', onclick: closeMenu })
  const menu = h('div', { class: 'menu', role: 'menu' },
    items.map((item) => (item === 'divider'
      ? h('div', { class: 'menu__divider', role: 'separator' })
      : h('button', {
        class: ['menu__item', item.destructive && 'row--destructive'], type: 'button', role: 'menuitem',
        onclick: () => { closeMenu(); item.onSelect?.() },
      },
      h('span', { class: 'menu__check' }, item.checked ? icon('checkmark', { size: 16 }) : null),
      item.icon ? icon(item.icon, { size: 18 }) : null,
      h('span', { class: 'row__main' }, h('span', null, item.label), item.detail ? h('span', { class: 'row__subtitle' }, item.detail) : null)))))
  document.body.append(scrim, menu)
  const width = menu.offsetWidth
  const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))
  menu.style.left = `${left}px`
  menu.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 8)}px`
  const onKey = (event) => { if (event.key === 'Escape') closeMenu() }
  document.addEventListener('keydown', onKey)
  current = { scrim, menu, onKey }
  menu.querySelector('button')?.focus()
}

let current = null

export function closeMenu() {
  if (!current) return
  current.scrim.remove()
  current.menu.remove()
  document.removeEventListener('keydown', current.onKey)
  current = null
}
