// The connect sheet: how this page gets its weather. On the phone the radio belongs to the whole
// app and the tool only reads its state; here the tool is the app, so it owns the connection too.
import { h } from './kit/dom.js'
import { Card, Row, Note, List, Banner, Spinner } from './kit/components.js'
import { t } from '../l10n.js'
import { RadioConnection, LinkKind } from '../app/RadioConnection.js'

export function openConnectSheet(app) {
  const connection = app.connection
  let handle = null
  const unsubscribe = connection.subscribe(() => handle?.refresh())

  const connect = (kind) => {
    connection.connect(kind).then(() => {
      // A radio that connected is the answer to why the sheet was open.
      if (connection.state === 'connected') handle?.close()
    }).catch(() => handle?.refresh())
  }

  const render = () => {
    const { state, kind, label, error } = connection
    const secure = globalThis.isSecureContext !== false
    const status = state === 'connected'
      ? (label ? t('web.connect.status.connected', kind === LinkKind.demo ? t('web.connect.demo.label') : label) : t('web.connect.status.connectedUnnamed'))
      : state === 'connecting' ? t('web.connect.status.connecting')
        : connection.reconnectTimer ? t('web.connect.status.reconnecting') : t('web.connect.status.disconnected')

    const details = []
    if (state === 'connected' && connection.isRadio) {
      if (connection.firmwareSupportsWeather === false) details.push(Banner({ text: t('web.connect.firmware.old', connection.firmwareVersion ?? '?') }))
      else if (connection.firmwareVersion) details.push(Note(t('web.connect.firmware.ok', connection.firmwareVersion)))
      if (!connection.isChannelSyncDone) details.push(Note(t('web.connect.channel.reading')))
      else if (connection.weatherSlot != null) details.push(Note(t('web.connect.channel.present', connection.weatherSlot)))
      else details.push(Note(t('web.connect.channel.missing')))
    }

    return List(
      Card({},
        Row({
          icon: state === 'connecting' ? Spinner() : 'antenna.radiowaves.left.and.right',
          title: status, chevron: false,
          trailing: state !== 'disconnected'
            ? h('button', { class: 'button button--small button--plain button--destructive', type: 'button', onclick: () => connection.disconnect() }, t('web.connect.disconnect'))
            : null,
        })),
      details,
      error ? Banner({ text: t('web.connect.error', error) }) : null,

      Card({ label: t('web.connect.section.radio') },
        Row({
          icon: 'wave.3.right', title: t('web.connect.bluetooth'),
          subtitle: RadioConnection.supportsBluetooth ? t('web.connect.bluetooth.detail') : t('web.connect.bluetooth.unsupported'),
          onclick: RadioConnection.supportsBluetooth && secure ? () => connect(LinkKind.bluetooth) : null,
          muted: !RadioConnection.supportsBluetooth, key: 'ble',
        }),
        Row({
          icon: 'cable.connector', title: t('web.connect.serial'),
          subtitle: RadioConnection.supportsSerial ? t('web.connect.serial.detail') : t('web.connect.serial.unsupported'),
          onclick: RadioConnection.supportsSerial && secure ? () => connect(LinkKind.serial) : null,
          muted: !RadioConnection.supportsSerial, key: 'usb',
        })),
      Note(secure ? t('web.connect.oneCompanion') : t('web.connect.secure')),

      Card({ label: t('web.connect.section.other') },
        connection.isBridgeAvailable
          ? Row({ icon: 'globe', title: t('web.connect.bridge'), subtitle: t('web.connect.bridge.detail'), onclick: () => connect(LinkKind.bridge), key: 'bridge' })
          : null,
        Row({ icon: 'clock', title: t('web.connect.demo'), subtitle: t('web.connect.demo.detail'), onclick: () => connect(LinkKind.demo), key: 'demo' })))
  }

  handle = app.nav.sheet({ title: () => t('web.connect.title'), render, onDismiss: unsubscribe })
  return handle
}

/** The pill in the navigation bar, until the radio screens supply theirs. */
export function DefaultRadioPill({ app }) {
  const { state, label, kind } = app.connection
  const text = state === 'connected'
    ? t('web.connect.pill.connected', kind === LinkKind.demo ? t('web.connect.demo.label') : (label ?? t('web.connect.status.connectedUnnamed')))
    : state === 'connecting' ? t('web.connect.pill.connecting') : t('web.connect.pill.disconnected')
  return h('button', {
    class: ['pill', state === 'connected' && 'pill--up', state === 'connecting' && 'pill--busy'],
    type: 'button', onclick: () => app.openConnect(),
  }, text)
}
