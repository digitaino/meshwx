// Web only. The small status pill in the navigation bar: whether this tab has a radio, and the
// way to the connect sheet.
//
// The iOS tool has no such control — a phone's radio is the app's own connection and is said in
// the radio row at the foot of a place page (docs/MESHWX_UI.md §10). A browser tab holds its
// link itself, can lose it on a reload, and has to be asked for a device by a gesture, so the
// state belongs where it is always in view. It says what is true and nothing more: the row and
// the radio page still carry everything about the *weather* radio.
import { h } from '../kit/dom.js'
import { t } from '../../l10n.js'

export function RadioPill({ app }) {
  const connection = app?.connection ?? null
  const state = connection?.state ?? 'disconnected'
  const label = connection?.label ?? null

  const text = state === 'connected'
    ? label ?? t('web.radio.pill.connected')
    : state === 'connecting'
      ? t('web.radio.pill.connecting')
      : t('web.radio.pill.disconnected')

  return h('button', {
    class: ['pill', state === 'connected' && 'pill--up', state === 'connecting' && 'pill--busy'],
    type: 'button',
    key: 'radio-pill',
    'aria-label': `${text}. ${t('web.radio.pill.action')}`,
    onclick: () => app?.openConnect?.(),
  }, h('span', null, text))
}
