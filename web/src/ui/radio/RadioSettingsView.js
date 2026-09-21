// Web only. The radio's own settings, from this page.
//
// The iOS app has no equivalent screen inside the weather tool: a phone's radio is the whole app's
// connection and is configured in the app's Settings. A browser tab that holds a radio over Web
// Bluetooth or Web Serial *is* the companion, and there is nowhere else to go. The owner plugged in
// a factory-fresh radio, found it deaf — it was on the firmware's own frequency, not the mesh's —
// and there was no way to move it. Their words: *"we need a way to set those in the web app itself.
// and the node name, etc"* (20 September 2026).
//
// Two rules run through the whole screen:
//
//  1. **Nothing is written until its own button is tapped.** Every card owns one write and says in
//     place whether it is going out, went out, or was refused and why. Typing changes nothing on
//     the radio.
//  2. **What is shown is what the radio says.** Every write re-reads self info and the fields are
//     re-seeded from it, so a value the radio clamped, truncated or refused shows as the radio has
//     it and not as it was typed.
//
// A radio is only heard by radios on *exactly* the same frequency, bandwidth, spreading factor and
// coding rate, so the radio card carries the tally of what has arrived since this page connected.
// Silence there is the one symptom a wrongly-tuned radio has: it connects, it answers, it names
// itself, and it hears nobody.
//
// The one screen that reaches into `src/radio` (docs/PORTING.md §9). What it takes from there is
// the pure rules only — the preset table, the firmware's ranges, the event tally and the name of an
// error code. Every byte still goes through `RadioConnection`.
import { h } from '../kit/dom.js'
import { Banner, Button, Card, Field, List, Prose, Row, Select, Switch } from '../kit/components.js'
import { t } from '../../l10n.js'
import {
  RadioActivity,
  RadioParameters,
  RadioPresets,
  errorCodeName,
} from '../../radio/index.js'
import { Line, Loading, safeScreen } from './support.js'

/**
 * A **pushed** screen, not a sheet, although it is reached from one: the connect sheet closes
 * itself first (`openRadioSettings`). A sheet over a sheet is a thing the navigation cannot take
 * apart — closing the inner one sends a back that the outer one then answers as well — and a page
 * of seven cards wants a title bar and a back button anyway.
 */
export function RadioSettingsScreen({ app }) {
  const connection = app.connection
  // The screen is re-rendered on every connection change already (`main.js` subscribes the
  // navigation), so this only has to cover the screen's own state.
  const refresh = () => app.nav.refresh()

  const state = {
    // Drafts. `null` in a field means "whatever the radio says".
    name: null,
    frequency: null, bandwidth: null, spreadingFactor: null, codingRate: null,
    txPower: null,
    latitude: null, longitude: null,
    // The radio's own values the drafts were seeded from. When the radio reports something else —
    // which happens after every write — the drafts are dropped and the radio wins.
    seededFrom: null,
    // One entry per card: `{ status: 'pending' | 'done' | 'error', message? }`.
    writes: {},
    locating: false,
    locationRefused: false,
  }

  /** The radio's own values, or nulls when there is no radio to ask. */
  const settings = () => connection.radioSettings ?? {}

  /**
   * Drops every draft when the radio's values have moved under them. The fingerprint is over the
   * values themselves, not the object, so a re-render that changed nothing leaves typing alone.
   */
  const reseedIfRadioChanged = () => {
    const now = settings()
    const fingerprint = JSON.stringify([
      now.name, now.frequency, now.bandwidth, now.spreadingFactor, now.codingRate,
      now.txPower, now.maxTxPower, now.latitude, now.longitude, now.manualAddContacts,
    ])
    if (state.seededFrom === fingerprint) return
    state.seededFrom = fingerprint
    state.name = null
    state.frequency = null
    state.bandwidth = null
    state.spreadingFactor = null
    state.codingRate = null
    state.txPower = null
    state.latitude = null
    state.longitude = null
  }

  /** A field's draft, falling back to what the radio says. Always a string, for the input. */
  const draft = (field, radioValue) => (state[field] ?? (radioValue == null ? '' : String(radioValue)))

  /** Which card owns each field, so editing one clears that card's stale "the radio has it". */
  const CARD_OF = {
    name: 'name',
    frequency: 'radio', bandwidth: 'radio', spreadingFactor: 'radio', codingRate: 'radio',
    txPower: 'power',
    latitude: 'position', longitude: 'position',
  }

  /**
   * Types into a field. The card's last outcome goes with it: "The radio has it" over a field that
   * has since been changed is a sentence about something that is no longer on screen.
   */
  const edit = (field, value) => {
    state[field] = value
    delete state.writes[CARD_OF[field]]
    refresh()
  }

  /**
   * Runs one write, with its own status. Failures are shown, never thrown: a refused write is a
   * sentence in the card it belongs to, not a broken screen.
   */
  const write = async (key, action, pending = null) => {
    // `pending` carries what the card should show while the write is out — a switch that has been
    // flipped has no draft to fall back on, and snapping it back for the length of one exchange
    // would read as the tap having missed.
    state.writes[key] = { status: 'pending', ...pending }
    refresh()
    try {
      await action()
      state.writes[key] = { status: 'done' }
    } catch (error) {
      state.writes[key] = { status: 'error', message: errorSentence(error) }
    }
    refresh()
  }

  const isPending = (key) => state.writes[key]?.status === 'pending'

  /** The status line a card shows under its button: going out, went out, or refused and why. */
  const status = (key, { done = t('web.radiosettings.status.done') } = {}) => {
    const entry = state.writes[key]
    if (entry == null) return null
    if (entry.status === 'pending') return Loading(t('web.radiosettings.status.pending'))
    if (entry.status === 'error') return Line(entry.message, { warn: true, key: `${key}-status` })
    return Line(done, { key: `${key}-status` })
  }

  // MARK: - The cards

  const nameCard = () => {
    const radio = settings()
    const value = draft('name', radio.name)
    const bytes = RadioParameters.nameByteLength(value)
    const tooLong = bytes > RadioParameters.nameMaxBytes
    const changed = value !== (radio.name ?? '')
    return Card({
      label: t('web.radiosettings.name.label'),
      key: 'name',
      foot: t('web.radiosettings.name.foot'),
    },
    Field({
      key: 'name-field',
      label: t('web.radiosettings.name.field'),
      value,
      wide: true,
      detail: t('web.radiosettings.name.bytes', bytes, RadioParameters.nameMaxBytes),
      invalid: tooLong || value.trim() === '',
      oninput: (next) => edit('name', next),
    }),
    radio.key ? Row({ key: 'name-key', title: t('web.radiosettings.name.key'), value: h('span', { class: 'mono' }, radio.key), chevron: false }) : null,
    changed && !tooLong ? Line(t('web.radiosettings.unsaved'), { key: 'name-unsaved' }) : null,
    h('div', { class: 'sheet__actions' }, Button({
      label: t('web.radiosettings.name.save'),
      kind: 'primary',
      disabled: isPending('name') || tooLong || value.trim() === '' || !changed,
      onclick: () => write('name', () => connection.renameRadio(value)),
    })),
    status('name'))
  }

  const radioCard = () => {
    const radio = settings()
    const typed = {
      frequency: RadioParameters.parseDecimal(draft('frequency', radio.frequency)),
      bandwidth: RadioParameters.parseDecimal(draft('bandwidth', radio.bandwidth)),
      spreadingFactor: RadioParameters.parseDecimal(draft('spreadingFactor', radio.spreadingFactor)),
      codingRate: RadioParameters.parseDecimal(draft('codingRate', radio.codingRate)),
    }
    const check = RadioParameters.validateRadio(typed)
    const preset = check.ok ? RadioPresets.matchingPreset(typed) : null
    const changed = !RadioParameters.sameRadioParameters(typed, radio)
    const heard = RadioActivity.heard(connection.radioEvents)

    return Card({
      label: t('web.radiosettings.radio.label'),
      key: 'radio',
      foot: t('web.radiosettings.radio.foot'),
    },
    Select({
      key: 'preset',
      label: t('web.radiosettings.radio.preset'),
      value: preset?.id ?? 'custom',
      options: presetOptions(),
      onchange: (id) => applyPresetToFields(id),
    }),
    Field({
      key: 'frequency',
      label: t('web.radiosettings.radio.frequency'),
      value: draft('frequency', radio.frequency),
      unit: t('web.radiosettings.radio.frequency.unit'),
      inputmode: 'decimal',
      invalid: problemFor(check, 'frequency') != null,
      oninput: (next) => edit('frequency', next),
    }),
    Select({
      key: 'bandwidth',
      label: t('web.radiosettings.radio.bandwidth'),
      value: bandwidthOptionValue(draft('bandwidth', radio.bandwidth)),
      options: bandwidthOptions(draft('bandwidth', radio.bandwidth)),
      onchange: (next) => edit('bandwidth', next),
    }),
    Select({
      key: 'sf',
      label: t('web.radiosettings.radio.spreadingFactor'),
      value: draft('spreadingFactor', radio.spreadingFactor),
      options: RadioParameters.spreadingFactors.map((value) => ({ value, label: String(value) })),
      onchange: (next) => edit('spreadingFactor', next),
    }),
    Select({
      key: 'cr',
      label: t('web.radiosettings.radio.codingRate'),
      options: RadioParameters.codingRates.map((value) => ({ value, label: String(value) })),
      value: draft('codingRate', radio.codingRate),
      onchange: (next) => edit('codingRate', next),
    }),
    // The one symptom a wrongly-tuned radio has. Nothing heard is not proof of a wrong preset —
    // the mesh may simply be out of reach — but it is the first thing the four values above are
    // worth checking against.
    heard.total === 0
      ? Row({ key: 'heard', title: t('web.radiosettings.radio.heard'), value: t('web.radiosettings.radio.heard.none'), chevron: false, stale: true })
      : [
        Row({ key: 'heard-adverts', title: t('web.radiosettings.radio.heard.adverts'), value: String(heard.adverts), chevron: false }),
        Row({ key: 'heard-messages', title: t('web.radiosettings.radio.heard.messages'), value: String(heard.messages), chevron: false }),
      ],
    check.problems.map((problem) => Line(problemSentence(problem), { warn: true, key: `problem-${problem.field}` })),
    changed && check.ok ? Line(t('web.radiosettings.unsaved'), { key: 'radio-unsaved' }) : null,
    // Enabled even when nothing was changed, unlike the other cards: writing the values the radio
    // already claims is a real remedy for a radio that says one thing and hears another, and it
    // costs one command.
    h('div', { class: 'sheet__actions' }, Button({
      label: t('web.radiosettings.radio.apply'),
      kind: 'primary',
      disabled: isPending('radio') || !check.ok,
      onclick: () => write('radio', () => connection.applyRadioParams(typed)),
    })),
    status('radio'))
  }

  /** Choosing a preset fills the four fields and sends nothing. */
  const applyPresetToFields = (id) => {
    const preset = RadioPresets.byId(id)
    if (preset == null) return                                   // "Custom" leaves them as they are
    state.frequency = String(preset.frequencyMHz)
    state.bandwidth = String(preset.bandwidthKHz)
    state.spreadingFactor = String(preset.spreadingFactor)
    state.codingRate = String(preset.codingRate)
    delete state.writes.radio
    refresh()
  }

  const powerCard = () => {
    const radio = settings()
    const maximum = radio.maxTxPower
    const known = Number.isFinite(maximum) && maximum >= RadioParameters.txPowerFloor
    const value = draft('txPower', radio.txPower)
    const power = RadioParameters.parseDecimal(value)
    const check = RadioParameters.validateTxPower(power, { maxTxPower: maximum })
    const changed = power !== radio.txPower
    return Card({
      label: t('web.radiosettings.power.label'),
      key: 'power',
      foot: t('web.radiosettings.power.foot'),
    },
    known
      ? Select({
        key: 'power-field',
        label: t('web.radiosettings.power.field'),
        value,
        options: powerOptions(maximum),
        onchange: (next) => edit('txPower', next),
      })
      : Line(t('web.radiosettings.power.unknown'), { warn: true, key: 'power-unknown' }),
    known && changed && check.ok ? Line(t('web.radiosettings.unsaved'), { key: 'power-unsaved' }) : null,
    known
      ? h('div', { class: 'sheet__actions' }, Button({
        label: t('web.radiosettings.power.save'),
        kind: 'primary',
        disabled: isPending('power') || !check.ok || !changed,
        onclick: () => write('power', () => connection.setTxPower(power)),
      }))
      : null,
    status('power'))
  }

  const positionCard = () => {
    const radio = settings()
    const typed = {
      latitude: RadioParameters.parseDecimal(draft('latitude', radio.latitude)),
      longitude: RadioParameters.parseDecimal(draft('longitude', radio.longitude)),
    }
    const check = RadioParameters.validatePosition(typed)
    const changed = typed.latitude !== radio.latitude || typed.longitude !== radio.longitude
    const shares = (radio.advertisementLocationPolicy ?? 0) > 0
    return Card({
      label: t('web.radiosettings.position.label'),
      key: 'position',
      foot: t('web.radiosettings.position.foot'),
    },
    Field({
      key: 'latitude',
      label: t('web.radiosettings.position.latitude'),
      value: draft('latitude', radio.latitude),
      inputmode: 'decimal',
      invalid: problemFor(check, 'latitude') != null,
      oninput: (next) => edit('latitude', next),
    }),
    Field({
      key: 'longitude',
      label: t('web.radiosettings.position.longitude'),
      value: draft('longitude', radio.longitude),
      inputmode: 'decimal',
      invalid: problemFor(check, 'longitude') != null,
      oninput: (next) => edit('longitude', next),
    }),
    Row({
      key: 'position-shared',
      title: t('web.radiosettings.position.shared'),
      value: shares ? t('web.radiosettings.yes') : t('web.radiosettings.no'),
      chevron: false,
    }),
    check.problems.map((problem) => Line(problemSentence(problem), { warn: true, key: `problem-${problem.field}` })),
    changed && check.ok ? Line(t('web.radiosettings.unsaved'), { key: 'position-unsaved' }) : null,
    state.locating ? Loading(t('web.radiosettings.position.locating')) : null,
    state.locationRefused ? Line(t('web.radiosettings.position.refused'), { warn: true, key: 'position-refused' }) : null,
    h('div', { class: 'sheet__actions cluster' },
      Button({
        label: t('web.radiosettings.position.save'),
        kind: 'primary',
        disabled: isPending('position') || !check.ok || !changed,
        onclick: () => write('position', () => connection.setPosition(typed)),
      }),
      // The one control that may raise the browser's location prompt, and only from this tap
      // (`src/platform/location.js`: nothing asks for location until somebody asks for it).
      Button({
        label: t('web.radiosettings.position.useBrowser'),
        icon: 'location.fill',
        disabled: state.locating,
        onclick: () => useBrowserLocation(),
      })),
    status('position'))
  }

  const useBrowserLocation = async () => {
    state.locating = true
    state.locationRefused = false
    delete state.writes.position
    refresh()
    let sample = null
    try { sample = await app.location?.request?.() ?? null } catch { sample = null }
    state.locating = false
    if (sample == null) state.locationRefused = true
    else {
      state.latitude = String(round6(sample.latitude))
      state.longitude = String(round6(sample.longitude))
    }
    refresh()
  }

  const contactsCard = () => {
    const radio = settings()
    // The firmware field is `manualAddContacts`: true means a node it hears is *not* kept until
    // somebody adds it. The switch says the plain thing, so it is the inverse of the field — the
    // one place in this file where a value is flipped, and the only place it should be.
    const inFlight = state.writes.contacts?.status === 'pending' ? state.writes.contacts.wanted : null
    const addsByItself = inFlight ?? (radio.manualAddContacts === false)
    return Card({
      label: t('web.radiosettings.contacts.label'),
      key: 'contacts',
      foot: t('web.radiosettings.contacts.foot'),
    },
    Row({
      key: 'auto-add',
      title: t('web.radiosettings.contacts.autoAdd'),
      subtitle: t('web.radiosettings.contacts.autoAdd.detail'),
      chevron: false,
      trailing: Switch({
        checked: addsByItself,
        disabled: isPending('contacts') || radio.manualAddContacts == null,
        label: t('web.radiosettings.contacts.autoAdd'),
        onchange: (wanted) => write('contacts', () => connection.setManualAddContacts(!wanted), { wanted }),
      }),
    }),
    status('contacts'))
  }

  const advertCard = () => Card({
    label: t('web.radiosettings.advert.label'),
    key: 'advert',
    foot: t('web.radiosettings.advert.foot'),
  },
  h('div', { class: 'sheet__actions cluster' },
    Button({
      label: t('web.radiosettings.advert.flood'),
      disabled: isPending('advert'),
      onclick: () => write('advert', () => connection.sendAdvert({ flood: true })),
    }),
    Button({
      label: t('web.radiosettings.advert.zeroHop'),
      disabled: isPending('advert'),
      onclick: () => write('advert', () => connection.sendAdvert({ flood: false })),
    })),
  status('advert', { done: t('web.radiosettings.advert.sent') }))

  const rebootCard = () => Card({
    label: t('web.radiosettings.reboot.label'),
    key: 'reboot',
    foot: t('web.radiosettings.reboot.foot'),
  },
  h('div', { class: 'sheet__actions' }, Button({
    label: t('web.radiosettings.reboot.button'),
    kind: 'destructive',
    block: true,
    disabled: isPending('reboot'),
    onclick: () => confirmReboot(),
  })),
  status('reboot', { done: t('web.radiosettings.reboot.sent') }))

  /** The confirmation names the radio it was opened for, as the clear sheet on the radio page does. */
  const confirmReboot = () => {
    const name = connection.label ?? settings().name ?? ''
    app.nav.sheet({
      title: () => t('web.radiosettings.reboot.confirm.title', name),
      done: t('weather.common.cancel'),
      render: (confirmation) => List(
        Card({}, Prose(t('web.radiosettings.reboot.confirm.message'))),
        h('div', { class: 'sheet__actions' }, Button({
          label: t('web.radiosettings.reboot.confirm.action'),
          kind: 'destructive',
          block: true,
          onclick: () => {
            confirmation.close()
            write('reboot', () => connection.rebootRadio())
          },
        }))),
    })
  }

  const render = () => {
    reseedIfRadioChanged()
    if (!connection.canConfigureRadio) {
      return List(Card({}, Prose(t('web.radiosettings.noRadio'))))
    }
    const heard = RadioActivity.heard(connection.radioEvents)
    return List(
      heard.total === 0 ? Banner({ text: t('web.radiosettings.deaf'), key: 'deaf' }) : null,
      nameCard(),
      radioCard(),
      powerCard(),
      positionCard(),
      contactsCard(),
      advertCard(),
      rebootCard(),
    )
  }

  return safeScreen({
    id: 'radiosettings',
    title: () => t('web.radiosettings.title'),
    render,
  })
}

/** The picker's options: Custom first, then the presets grouped by region. */
function presetOptions() {
  return [
    { value: 'custom', label: t('web.radiosettings.radio.custom') },
    // A preset's name is data, not copy: the community's own names, as the app lists them. The four
    // numbers are not in the label — a native select clips a long option, and choosing one fills
    // the four fields right below, which is where they can be read.
    ...RadioPresets.grouped().map((group) => ({
      group: t(`web.radiosettings.region.${group.region}`),
      options: group.presets.map((preset) => ({ value: preset.id, label: preset.name })),
    })),
  ]
}

/**
 * The bandwidth options, with the radio's own value added when it is not one of the standard ten.
 * A radio can be on anything the firmware accepted; a picker that could not show what the radio is
 * actually on would misreport it.
 */
function bandwidthOptions(current) {
  const values = [...RadioParameters.bandwidths]
  const typed = RadioParameters.parseDecimal(current)
  if (typed != null && !values.includes(typed)) values.push(typed)
  values.sort((a, b) => a - b)
  return values.map((value) => ({ value, label: t('web.radiosettings.radio.bandwidth.option', value) }))
}

/** The option value matching a bandwidth draft, so `62.50` selects the `62.5` option. */
function bandwidthOptionValue(current) {
  const typed = RadioParameters.parseDecimal(current)
  return typed == null ? '' : String(typed)
}

function powerOptions(maximum) {
  const options = []
  for (let dBm = RadioParameters.txPowerFloor; dBm <= maximum; dBm += 1) {
    options.push({ value: dBm, label: t('web.radiosettings.power.option', dBm) })
  }
  return options
}

function problemFor(check, field) {
  return check.problems.find((problem) => problem.field === field) ?? null
}

/** One sentence per rejected field, naming the range rather than saying "invalid". */
function problemSentence(problem) {
  const range = {
    frequency: RadioParameters.frequencyRangeMHz,
    bandwidth: RadioParameters.bandwidthRangeKHz,
    spreadingFactor: RadioParameters.spreadingFactorRange,
    codingRate: RadioParameters.codingRateRange,
    latitude: RadioParameters.latitudeRange,
    longitude: RadioParameters.longitudeRange,
  }[problem.field]
  if (range == null) return t('web.radiosettings.invalid.other')
  return t(`web.radiosettings.invalid.${problem.field}`, String(range.lowerBound), String(range.upperBound))
}

/** What went wrong, in the radio's words where it had any. */
function errorSentence(error) {
  if (error?.kind === 'timeout') return t('web.radiosettings.error.timeout')
  if (error?.kind === 'notConnected') return t('web.radiosettings.error.notConnected')
  if (error?.kind === 'deviceError') {
    const name = errorCodeName(error.code)
    if (name === 'illegalArgument') return t('web.radiosettings.error.illegalArgument')
    if (name === 'unsupportedCommand') return t('web.radiosettings.error.unsupported')
    if (name === 'badState') return t('web.radiosettings.error.badState')
    return t('web.radiosettings.error.device', error.code ?? 0)
  }
  return t('web.radiosettings.error.generic', error?.message ?? String(error))
}

function round6(value) {
  return Math.round(value * 1e6) / 1e6
}
