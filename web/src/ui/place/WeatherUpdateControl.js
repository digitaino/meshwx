// Port of MC1/Views/Tools/Weather/WeatherUpdateControl.swift
//
// The Update button (docs/MESHWX_UI.md §11). It is in the tool's bottom bar on a place screen and
// above the readings on a station screen, in the same position whatever the screen holds. It
// plans the minimal request set from what the phone is actually missing, **says so before it
// sends anything**, and sends the steps five seconds apart.
//
// **Pull-to-refresh does not exist on the web**, so the button is the whole of the refresh. That
// suits it: §3.1 O-4 preferred a button precisely because "a button can say what it will ask for,
// a gesture cannot", and the caption under the weather is where it says it (§3.1.2 V-5).

import { h } from '../kit/dom.js'
import { Spinner } from '../kit/components.js'
import { t } from '../../l10n.js'
import { WeatherUpdatePlan } from '../../screen/index.js'
import { WeatherAskFootnotes } from './WeatherAskControl.js'
import {
  attempt, everythingCurrent, nowOf, requestBlocked, screenValue, sourceNameOf, updateAsks, updateJustReceived,
} from './support.js'

/**
 * What a tap will ask for; while a run is on the air, or just back, what it said; and, when
 * nothing can be asked at all, the reason.
 *
 * A page still being built says nothing: there is no plan for it yet, and naming the last page's
 * would be the bug the per-page build is here to prevent (docs/MESHWX_UI.md §3.1 P-1).
 */
export function updateCaption({ app, screen, plan }) {
  if (screen?.snapshot == null) return ''
  const source = sourceNameOf(screen)
  const block = screen.snapshot.requestBlock ?? null
  // The blocking reason **is** the caption: a separate "blocked" line printed the same sentence
  // one line below it on every station screen (docs/MESHWX_UI.md §3.1 U-5).
  if (block != null) return requestBlocked(block, { source }) ?? ''

  const status = screenValue(screen, 'updateStatusText', (one) => one.model?.updateStatusText?.({ pageID: one.pageID }))
  if (status) return status

  const held = plan ?? WeatherUpdatePlan.empty
  if (!attempt(() => WeatherUpdatePlan.isEmpty(held), true)) return updateAsks(held, { source }) ?? ''
  // Held back by the five-minute rule is not the same as current, and never claims to be.
  if ((held.justReceived ?? []).length > 0) return updateJustReceived({ source }) ?? ''
  return everythingCurrent({
    source,
    asOf: held.currentAsOf ?? null,
    now: nowOf(screen, app),
    timeZone: app?.timeZone,
    locale: app?.locale,
  }) ?? ''
}

/**
 * @param {object} options
 * @param {object} options.app
 * @param {object|null} options.screen   the page (or station screen) this Update speaks for
 * @param {object} options.plan          that page's `WeatherUpdatePlan`
 * @param {boolean} [options.showsCaption] a station screen shows the caption under the button; a
 *   place page shows it under the weather, where the plan belongs (§3.1.2 V-5)
 */
export function WeatherUpdateControl({ app, screen, plan, showsCaption = false }) {
  const held = plan ?? WeatherUpdatePlan.empty
  const block = screen?.snapshot?.requestBlock ?? null
  const caption = updateCaption({ app, screen, plan: held })

  // **§11.2: offline, "Connect your radio to ask WX-AUS" goes *in place of* the button.** On the
  // web that place is a control that opens the connect sheet, so the bar both says it and fixes
  // it, and the page's caption stands down rather than printing the sentence twice (§3.1 U-24).
  // The whole sentence is the button's accessible description; the bar has room for the short
  // label beside Places and the dots.
  if (!showsCaption && block === 'radioOffline' && typeof app?.openConnect === 'function') {
    return h('button', {
      class: 'button button--plain button--strong',
      type: 'button',
      'aria-description': caption || null,
      title: caption || null,
      onclick: () => attempt(() => app.openConnect()),
    }, h('span', null, t('web.place.connectRadio')))
  }

  const isUpdating = screenValue(screen, 'isUpdating', (one) => one.model?.isUpdating?.({ pageID: one.pageID })) === true
  const isEmpty = attempt(() => WeatherUpdatePlan.isEmpty(held), true)
  const disabled = screen == null || isEmpty || isUpdating || block != null

  const button = h('button', {
    class: ['button', showsCaption ? null : 'button--plain', 'button--strong'],
    type: 'button',
    disabled,
    'aria-label': t('weather.update.button'),
    // What the tap will spend, read out with the button, exactly as the Swift's
    // `accessibilityValue` does.
    'aria-description': caption || null,
    title: caption || null,
    dataset: { test: 'weather.update.button' },
    onclick: () => {
      if (screen == null) return
      attempt(() => screen.model?.update?.(held, { pageID: screen.pageID }))
    },
  }, isUpdating ? Spinner() : null, h('span', null, t('weather.update.button')))

  if (!showsCaption) return button

  return h('div', { class: 'update' },
    // While everything is blocked the reason is the caption and there is nothing to press.
    block == null ? button : null,
    caption
      ? h('p', { class: 'footnote', dataset: { test: 'weather.update.caption' } }, caption)
      : null,
    screen != null && block == null ? WeatherAskFootnotes({ app, screen }) : null)
}
