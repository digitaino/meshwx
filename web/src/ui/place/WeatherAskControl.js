// Port of MC1/Views/Tools/Weather/WeatherAskControl.swift
//
// A button that puts one request on the air, with the request's status in place
// (docs/MESHWX_UI.md §11), the "everyone gets the answer" note, and the bar that shows a request
// whose button is not on the screen.
//
// Ask buttons are left only where they ask for something the screen is not otherwise about: METAR
// and TAF on a station screen, "Ask for latest" on a product screen. **A place page has none** —
// Update is the only way it spends airtime (§11.1).

import { h } from '../kit/dom.js'
import { Spinner } from '../kit/components.js'
import { t } from '../../l10n.js'
import { WeatherRequest } from '../../weather/index.js'
import { attempt, nowOf, ownedReply, quietCaption, sourceNameOf } from './support.js'

const isBlocked = (status) => status?.kind === 'blocked'
const isAskable = (status) => status == null || status.kind === 'idle' || status.kind === 'settled'
const isDisabled = (status) => status?.kind === 'pending' || status?.kind === 'waitingForOther' || isBlocked(status)

/**
 * @param {object} options
 * @param {object} options.app
 * @param {object} options.screen   the page this button is on, so the bot it names and the reason
 *   it is blocked are that page's and not the pager's last build (docs/MESHWX_UI.md §13)
 * @param {string} options.title
 * @param {object} options.request   a `WeatherRequest`
 * @param {boolean} [options.showsFootnotes] the first ask button on a screen carries the note
 * @param {boolean} [options.showsBlockReason] false on a screen that says it once already
 *   (§3.1 U-24)
 */
export function WeatherAskButton({ app, screen, title, request, showsFootnotes = false, showsBlockReason = true }) {
  const model = screen?.model ?? app?.model ?? null
  const source = sourceNameOf(screen)
  const status = attempt(() => model?.status?.({ for: request }))
  const text = attempt(() => model?.statusText?.({ for: request, source })) ?? null

  if (isBlocked(status) && showsBlockReason) {
    return h('div', { class: 'ask' }, h('p', { class: 'footnote' }, text ?? ''))
  }

  if (isAskable(status)) {
    const reply = attempt(() => model?.freshOwnedReply?.({ for: request }))
    if (reply != null) {
      // Named from the reply's own bot: the answer on screen came from whoever sent it.
      const answered = text ?? ownedReply({
        source: attempt(() => model?.botName?.(reply.botID)) ?? source,
        at: reply.assembly?.lastReceivedAt,
        now: nowOf(screen, app),
        timeZone: app?.timeZone,
        locale: app?.locale,
      })
      return h('div', { class: 'ask' }, h('p', { class: 'footnote' }, answered ?? ''))
    }
  }

  return h('div', { class: 'ask' },
    h('button', {
      class: 'button button--plain button--strong',
      type: 'button',
      disabled: isDisabled(status),
      // The blocked sentence may be the screen's rather than this row's; VoiceOver is not left
      // guessing why the button is disabled.
      'aria-description': text || null,
      onclick: () => attempt(() => model?.send?.(request)),
    }, status?.kind === 'pending' ? Spinner() : null, h('span', null, title)),
    text && !isBlocked(status) ? h('p', { class: 'footnote' }, text) : null,
    showsFootnotes ? WeatherAskFootnotes({ app, screen }) : null)
}

/** "Everyone listening on #meshwx gets the answer." and, for a quiet bot, that it may not answer. */
export function WeatherAskFootnotes({ app, screen }) {
  const since = screen?.snapshot?.sourceQuietSince ?? null
  const quiet = since == null
    ? null
    : quietCaption({
      source: sourceNameOf(screen),
      since,
      now: nowOf(screen, app),
      timeZone: app?.timeZone,
      locale: app?.locale,
    })
  return h('div', { class: 'ask__notes' },
    h('p', { class: 'footnote' }, t('weather.request.publicNote')),
    quiet ? h('p', { class: 'footnote footnote--warn' }, quiet) : null)
}

/**
 * A request on the air whose button is not on this screen, in a bar that stays in view above the
 * bottom edge rather than a row that scrolls away.
 *
 * @param {Set<string>} options.requestsOnScreen keys from `WeatherRequest.key`
 */
export function WeatherPendingBar({ app, requestsOnScreen = new Set() }) {
  const model = app?.model ?? null
  const request = attempt(() => model?.activeRequest) ?? null
  if (request == null) return null
  const key = attempt(() => WeatherRequest.key(request))
  if (key != null && requestsOnScreen.has(key)) return null
  const text = attempt(() => model?.statusText?.({ for: request })) ?? null
  if (!text) return null
  return h('div', { class: 'pending', role: 'status' }, Spinner(), h('span', null, text))
}

/** The keys of every request a screen's own controls already speak for. */
export function requestKeys(requests) {
  const keys = new Set()
  for (const request of requests ?? []) {
    const key = attempt(() => WeatherRequest.key(request))
    if (key != null) keys.add(key)
  }
  return keys
}
