// Port of MC1/Views/Tools/Weather/WeatherAskControl.swift.
//
// A button that puts one request on the air, with the request's status in place
// (docs/MESHWX_UI.md §11), and the bar that speaks for a request whose button is not on screen.
//
// A blocking reason replaces the button. From the tap until the answer the button is disabled
// with a spinner, and every other button is disabled and says so. `showsFootnotes` marks the
// first ask button on a screen: it carries the "everyone gets the answer" note and the quiet-bot
// caption.
import { h } from '../kit/dom.js'
import { Spinner } from '../kit/components.js'
import { t } from '../../l10n.js'
import { WeatherRequest } from '../../weather/index.js'
import { copy } from './support.js'

/** Whether a tap on this request would put something on the air (§17.1). */
export function isAskable(status) {
  return status?.kind === 'idle' || status?.kind === 'settled'
}

function isBlocked(status) {
  return status?.kind === 'blocked'
}

function isDisabled(status) {
  return status?.kind === 'pending' || status?.kind === 'waitingForOther' || status?.kind === 'blocked'
}

/**
 * `AskButton({ app, page, title, request, showsFootnotes, showsBlockReason })`.
 *
 * `showsBlockReason` is false on a screen that already says once why nothing can be asked: the
 * button then stays, disabled, with the reason in its accessibility value, which is the same fact
 * without the third telling (docs/MESHWX_UI.md §3.1 U-24).
 */
export function AskButton({ app, page, title, request, showsFootnotes = false, showsBlockReason = true, key = null }) {
  const model = app.model
  const status = model?.status?.({ for: request }) ?? null
  const text = model?.statusText?.({ for: request, source: page?.sourceName }) ?? null
  const words = copy(app, page)

  if (isBlocked(status) && showsBlockReason) {
    return h('div', { class: 'ask', key }, h('p', { class: 'line' }, text ?? ''))
  }

  const reply = isAskable(status) ? model?.freshOwnedReply?.({ for: request }) ?? null : null
  if (reply != null) {
    // Named from the reply's own bot: the answer on screen came from whoever sent it.
    const line = text ?? words.ownedReply(model.botName(reply.botID), reply.assembly.lastReceivedAt)
    return h('div', { class: 'ask', key }, h('p', { class: 'line' }, line))
  }

  const pending = status?.kind === 'pending'
  return h('div', { class: 'ask', key },
    h('button', {
      class: 'button button--plain button--strong ask__button',
      type: 'button',
      disabled: isDisabled(status) || model?.send == null,
      'aria-describedby': null,
      'aria-label': text ? `${title}. ${text}` : null,
      onclick: () => { Promise.resolve(model.send(request)).catch(() => {}) },
    }, pending ? Spinner() : null, h('span', null, title)),
    text != null && !isBlocked(status) ? h('p', { class: 'line', 'aria-hidden': 'true' }, text) : null,
    showsFootnotes ? AskFootnotes({ app, page }) : null)
}

/** "Everyone listening on #meshwx gets the answer." and, for a quiet bot, that it may not answer. */
export function AskFootnotes({ app, page }) {
  const words = copy(app, page)
  const quietSince = page?.snapshot?.sourceQuietSince ?? null
  return h('div', { class: 'ask__notes' },
    h('p', { class: 'line' }, t('weather.request.publicNote')),
    quietSince != null ? h('p', { class: 'line line--warn' }, words.quietCaption(page.sourceName, quietSince)) : null)
}

/**
 * A request on the air whose button is not on this screen, in a bar that stays in view above the
 * bottom edge rather than a row that scrolls away.
 *
 * `requestsOnScreen` is an array of requests; a request is matched by its `>` line, the one thing
 * two `WeatherRequest` values share when they are the same request.
 */
export function PendingBar({ app, requestsOnScreen = [] }) {
  const model = app.model
  const request = model?.activeRequest ?? null
  if (request == null) return null
  const wire = wireOf(request)
  if (wire != null && requestsOnScreen.some((one) => wireOf(one) === wire)) return null
  const text = model.statusText?.({ for: request }) ?? null
  if (text == null) return null
  return h('div', { class: 'pending', role: 'status', key: 'pending' }, Spinner(), h('span', null, text))
}

/** The same bar as an overlay, for a full-bleed map: it must not resize the map (§3.1 U-33). */
export function PendingOverlay({ app, requestsOnScreen = [] }) {
  const bar = PendingBar({ app, requestsOnScreen })
  if (bar == null) return null
  return h('div', { class: 'map__overlay map__overlay--bottom', key: 'pending-overlay' },
    h('div', { class: 'map__chip map__chip--pending' }, bar))
}

function wireOf(request) {
  if (request == null) return null
  try {
    return WeatherRequest.wireText(request)
  } catch {
    return null
  }
}
