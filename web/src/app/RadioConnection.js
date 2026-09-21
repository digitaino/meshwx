// The web app's stand-in for the iOS `AppState`: one object that owns the link to the weather
// (a radio over Bluetooth or USB, the development bridge, or the demo recording) and tells the
// model what it needs to know about it. It implements the `WeatherHost` interface
// (`WeatherHost.js`).
//
// The weather service outlives any one connection, because what the phone holds is kept between
// visits. Connecting swaps the service's transport and starts a new radio session; the order
// matters and is the iOS order: the service subscribes to datagrams **before** the session drains
// the radio's queue, so what the radio held while nobody was connected is seen, and seen as
// backlog rather than as live.
//
// A MeshCore radio has one companion at a time. While this page is connected it *is* the
// companion, so the radio hands it every queued message, chats included. Those are kept in
// `otherMessages` and shown on the radio page rather than dropped.

import {
  MeshCoreSession, RadioParameters, WebBluetoothTransport, WebSerialTransport,
} from '../radio/index.js'
import { SessionWeatherTransport, findWeatherSlot, addWeatherChannel } from '../link/SessionWeatherTransport.js'
import { RemoteBotWeatherTransport } from '../link/RemoteBotWeatherTransport.js'
import { ReplayWeatherTransport } from '../link/ReplayWeatherTransport.js'
import { WeatherChannel } from '../weather/index.js'
import { HeardBots } from './HeardBots.js'

const LAST_LINK_KEY = 'meshwx.link.last'
const OTHER_MESSAGES_KEY = 'other.messages'
const OTHER_MESSAGES_LIMIT = 50
const RECONNECT_DELAYS_MS = [1500, 4000, 10_000, 20_000]

/** A transport for the moments when nothing is connected: no datagrams, and nothing can be sent. */
class NoWeatherTransport {
  subscribeDatagrams() { return () => {} }
  subscribeAcknowledgements() { return () => {} }
  async sendRequest() { throw new Error('no radio connected') }
  async sendChannelRequest() { throw new Error('no radio connected') }
  async resetPath() {}
  async channelSecret() { return null }
  async isDrainingBacklog() { return false }
  async linkState() { return null }
}

export const LinkKind = Object.freeze({ bluetooth: 'bluetooth', serial: 'serial', bridge: 'bridge', demo: 'demo', simulated: 'simulated' })

export class RadioConnection {
  /**
   * @param {object} options
   * @param {import('../weather/index.js').WeatherService} options.weatherService
   * @param {object} options.kv          KeyValueStore
   * @param {object} options.location    LocationService
   * @param {object} options.notifications
   * @param {(message: string) => void} [options.log]
   */
  constructor({ weatherService, kv, location, notifications = null, log = () => {} }) {
    this.weatherService = weatherService
    // Until a link is up the service talks to nobody, but it still serves what was kept.
    weatherService.transport = new NoWeatherTransport()
    this.kv = kv
    this.location = location
    this.notifications = notifications
    this.log = log

    /** 'disconnected' | 'connecting' | 'connected' */
    this.state = 'disconnected'
    /** Which link is up or coming up: a `LinkKind`, or null. */
    this.kind = null
    /** What to call the link on screen: the radio's name, "WX-AUS bridge", "Demo". */
    this.label = null
    /** The last failure, as a sentence for the connect sheet. Cleared on the next attempt. */
    this.error = null
    this.radioSessionStartedAt = null
    this.firmwareSupportsWeather = null
    this.firmwareVersion = null
    this.contacts = []
    /** What the radio itself lists, and the weather bots heard advertising that it did not keep. */
    this.radioContacts = []
    this.heardBots = []
    /** How many of each kind of event the radio has sent this session: bring-up diagnostics. */
    this.radioEvents = {}
    this.radioSettings = null
    this.channels = []
    this.isChannelSyncDone = false
    this.maxChannels = 0
    this.otherMessages = []
    this.isBridgeAvailable = false
    /** The slot holding `#meshwx`, or null. */
    this.weatherSlot = null

    this.session = null
    this.transport = null
    /** The frame transport under the session: it holds the device the user granted. */
    this.frames = null
    this.listeners = new Set()
    this.sessionUnsubscribe = null
    this.userDisconnected = false
    this.reconnectAttempt = 0
    this.reconnectTimer = null
    this.contactsTimer = null

    this.isVisible = typeof document === 'undefined' ? true : document.visibilityState !== 'hidden'
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        this.isVisible = document.visibilityState !== 'hidden'
        this.#changed()
      })
    }
  }

  // MARK: WeatherHost

  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  get isRadioConnected() { return this.state === 'connected' }

  /** Whether the link is a real radio, as opposed to the bridge or the demo. */
  get isRadio() { return this.kind === LinkKind.bluetooth || this.kind === LinkKind.serial || this.kind === LinkKind.simulated }

  static get supportsBluetooth() { return WebBluetoothTransport.isSupported() }
  static get supportsSerial() { return WebSerialTransport.isSupported() }

  /** From a tap only. Writes `#meshwx` into the first free slot, then re-reads the table. */
  async addWeatherChannel() {
    if (!this.session) throw new Error('no radio connected')
    const result = await addWeatherChannel(this.session)
    await this.#syncChannels()
    return result
  }

  // MARK: Boot

  /** Restores what was kept and quietly tries the link used last time. Never prompts. */
  async start() {
    try { this.otherMessages = (await this.kv.get(OTHER_MESSAGES_KEY)) ?? [] } catch { this.otherMessages = [] }
    try { this.heardBots = HeardBots.fromStored(await this.kv.get(HeardBots.storageKey)) } catch { this.heardBots = [] }
    this.#probeBridge()
    // `?link=demo` (or `bridge`) opens that link without a tap: a shareable demo, and a way to
    // look at the tool from a script. Radios cannot be opened this way; they need a gesture.
    const asked = new URLSearchParams(globalThis.location?.search ?? '').get('link')
    const last = [LinkKind.demo, LinkKind.bridge, LinkKind.simulated].includes(asked) ? asked : this.#lastLink()
    if (last === LinkKind.serial) await this.#reconnectSerial()
    else if (last === LinkKind.bluetooth) await this.#reconnectBluetooth()
    else if (last === LinkKind.bridge || last === LinkKind.demo || last === LinkKind.simulated) this.connect(last).catch(() => {})
  }

  async #probeBridge() {
    try {
      const response = await fetch('api/bridge/info', { headers: { 'x-bridge-client': 'web' } })
      const body = response.ok ? await response.json() : null
      this.isBridgeAvailable = body?.ok === true
    } catch { this.isBridgeAvailable = false }
    this.#changed()
  }

  // MARK: Connecting

  /**
   * Connects a link. For `bluetooth` and `serial` this must run inside a user gesture: the
   * browser's device chooser opens from it.
   */
  async connect(kind, { granted = null } = {}) {
    await this.disconnect({ byUser: false, quiet: true })
    this.userDisconnected = false
    this.error = null
    this.kind = kind
    this.state = 'connecting'
    this.label = null
    this.#changed()
    try {
      if (kind === LinkKind.bluetooth) await this.#connectRadio(new WebBluetoothTransport(granted ? { device: granted } : {}))
      else if (kind === LinkKind.serial) await this.#connectRadio(new WebSerialTransport(granted ? { port: granted } : {}))
      else if (kind === LinkKind.bridge) await this.#connectLink(new RemoteBotWeatherTransport({ clientID: 'web', log: this.log }))
      else if (kind === LinkKind.demo) await this.#connectLink(await this.#demoTransport())
      else if (kind === LinkKind.simulated) {
        const { makeSimulatedRadio } = await import('../link/SimulatedRadio.js')
        const without = new URLSearchParams(globalThis.location?.search ?? '').has('nochannel')
        await this.#connectRadio(await makeSimulatedRadio({ withWeatherChannel: !without, log: this.log }))
      }
      else throw new Error(`unknown link ${kind}`)
      this.#rememberLink(kind)
      this.reconnectAttempt = 0
    } catch (error) {
      this.log(`connect ${kind}: ${error?.message ?? error}`)
      await this.#teardown()
      this.state = 'disconnected'
      this.kind = null
      // Closing the browser's chooser is not a failure worth a sentence.
      this.error = error?.name === 'NotFoundError' ? null : (error?.message ?? String(error))
      this.#changed()
      throw error
    }
  }

  async disconnect({ byUser = true, quiet = false } = {}) {
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    if (byUser) { this.userDisconnected = true; this.#rememberLink(null) }
    if (this.state === 'disconnected' && !this.session && !this.transport) return
    await this.#teardown()
    this.state = 'disconnected'
    this.kind = null
    this.label = null
    if (!quiet) this.#changed()
  }

  async #connectLink(transport) {
    this.transport = transport
    this.weatherService.transport = transport
    await this.weatherService.startEventMonitoring()
    const link = await transport.linkState()
    this.label = link?.bot?.name ?? null
    this.firmwareSupportsWeather = true
    this.firmwareVersion = null
    this.isChannelSyncDone = true
    this.channels = [{ index: 1, name: WeatherChannel.name, secret: await WeatherChannel.secret() }]
    this.weatherSlot = 1
    this.maxChannels = 40
    this.radioSessionStartedAt = Date.now()
    this.state = 'connected'
    this.#changed()
  }

  async #connectRadio(frames) {
    const session = new MeshCoreSession(frames)
    this.session = session
    this.frames = frames
    this.channels = []
    this.contacts = []
    this.radioContacts = []
    this.isChannelSyncDone = false
    // Per connection, as the field's own name says: the radio settings screen reads this tally as
    // "heard since connecting", and a count carried over from the last radio would answer the
    // wrong question.
    this.radioEvents = {}

    const transport = new SessionWeatherTransport({
      session,
      storedChannelSecret: async (index) => this.channels.find((c) => c.index === index)?.secret ?? null,
      storedWeatherSlot: async () => this.#weatherSlotFromTable(),
    })
    this.transport = transport
    this.weatherService.transport = transport
    // Before the session starts: the connect drain must find the service already listening.
    await this.weatherService.startEventMonitoring()
    this.sessionUnsubscribe = session.subscribe((event) => this.#onSessionEvent(event))

    await session.start()

    const device = session.deviceInfo ?? {}
    const self = session.selfInfo ?? {}
    this.#adoptSelfInfo(self, { fallbackName: frames.deviceName ?? null })
    this.firmwareSupportsWeather = session.supportsChannelDatagrams
    this.firmwareVersion = device.version || device.firmwareBuild || (device.firmwareVersion != null ? `v${device.firmwareVersion}` : null)
    this.maxChannels = device.maxChannels ?? 8
    this.radioSessionStartedAt = Date.now()
    this.state = 'connected'
    this.#changed()

    // After the screen can say "connected": the contacts name the weather radios, the channel
    // table says whether #meshwx is on this radio.
    await this.#syncContacts().catch((error) => this.log(`contacts: ${error.message}`))
    await this.#syncChannels().catch((error) => this.log(`channels: ${error.message}`))
  }

  async #demoTransport() {
    const response = await fetch('demo/datagrams.json')
    if (!response.ok) throw new Error('the demo recording is missing')
    return new ReplayWeatherTransport({ recording: await response.json() })
  }

  async #teardown() {
    this.sessionUnsubscribe?.()
    this.sessionUnsubscribe = null
    clearTimeout(this.contactsTimer)
    try { this.weatherService.stopEventMonitoring() } catch { /* already stopped */ }
    this.weatherService.transport = new NoWeatherTransport()
    const session = this.session
    this.session = null
    this.transport = null
    this.frames = null
    this.radioSessionStartedAt = null
    this.isChannelSyncDone = false
    if (session) await session.stop().catch(() => {})
  }

  // MARK: What the radio says about itself

  /**
   * What the radio is tuned to, what it is called, where it says it is. A radio on other values
   * than the mesh hears nothing and is heard by nothing, and looks exactly like a working one from
   * here — which is the whole reason the radio settings screen exists.
   */
  #adoptSelfInfo(self, { fallbackName = null } = {}) {
    this.label = self.name ?? fallbackName ?? null
    this.radioSettings = {
      name: self.name ?? null,
      frequency: self.radioFrequency ?? null, bandwidth: self.radioBandwidth ?? null,
      spreadingFactor: self.radioSpreadingFactor ?? null, codingRate: self.radioCodingRate ?? null,
      txPower: self.txPower ?? null, maxTxPower: self.maxTxPower ?? null,
      latitude: self.latitude ?? null, longitude: self.longitude ?? null,
      manualAddContacts: self.manualAddContacts ?? null,
      advertisementLocationPolicy: self.advertisementLocationPolicy ?? null,
      key: Array.from(self.publicKey?.subarray?.(0, 6) ?? [], (b) => b.toString(16).padStart(2, '0')).join(''),
    }
  }

  /** Whether this link's own settings can be read and written: a radio, connected. */
  get canConfigureRadio() {
    return this.isRadio && this.state === 'connected' && this.session != null
  }

  #radioSession() {
    if (!this.canConfigureRadio) throw new Error('no radio connected')
    return this.session
  }

  /**
   * Reads self info back after a write and republishes it, so the screen shows what the radio now
   * says rather than what was typed. A link swapped while the read was in flight is left alone.
   */
  async #rereadSelfInfo(session) {
    const self = await session.refreshSelfInfo()
    if (this.session !== session) return this.radioSettings
    this.#adoptSelfInfo(self)
    this.#changed()
    return this.radioSettings
  }

  /** From a tap only. Sets the advertised name; the radio truncates at 31 UTF-8 bytes. */
  async renameRadio(name) {
    const session = this.#radioSession()
    const wanted = (name ?? '').trim()
    if (wanted === '') throw new Error('a radio needs a name')
    await session.setName(wanted)
    return this.#rereadSelfInfo(session)
  }

  /**
   * From a tap only. Sets the four values that decide what this radio can hear. Validated here as
   * well as in the screen: a value outside the firmware's ranges is refused by the radio, and a
   * refusal leaves it on whatever it was on, which reads from a screen like a write that worked.
   */
  async applyRadioParams({ frequency, bandwidth, spreadingFactor, codingRate }) {
    const session = this.#radioSession()
    const check = RadioParameters.validateRadio({ frequency, bandwidth, spreadingFactor, codingRate })
    if (!check.ok) throw radioParameterError(check.problems)
    await session.setRadio({ frequency, bandwidth, spreadingFactor, codingRate })
    return this.#rereadSelfInfo(session)
  }

  /** From a tap only. Transmit power in dBm, between the firmware's floor and this radio's own. */
  async setTxPower(power) {
    const session = this.#radioSession()
    const check = RadioParameters.validateTxPower(power, { maxTxPower: this.radioSettings?.maxTxPower })
    if (!check.ok) throw radioParameterError(check.problems)
    await session.setTxPower(power)
    return this.#rereadSelfInfo(session)
  }

  /** From a tap only. The position the radio carries in its adverts, in degrees. */
  async setPosition({ latitude, longitude }) {
    const session = this.#radioSession()
    const check = RadioParameters.validatePosition({ latitude, longitude })
    if (!check.ok) throw radioParameterError(check.problems)
    await session.setCoordinates({ latitude, longitude })
    return this.#rereadSelfInfo(session)
  }

  /**
   * From a tap only. Whether the radio adds by itself the contacts it hears. The session reads the
   * other "other params" first and writes them back unchanged: the command has no partial form.
   */
  async setManualAddContacts(enabled) {
    const session = this.#radioSession()
    const self = await session.setManualAddContacts(enabled)
    if (this.session !== session) return this.radioSettings
    this.#adoptSelfInfo(self ?? session.selfInfo ?? {})
    this.#changed()
    return this.radioSettings
  }

  /** From a tap only. Broadcasts an advert, flooded through the mesh or one hop only. */
  async sendAdvert({ flood = false } = {}) {
    await this.#radioSession().sendAdvertisement({ flood })
  }

  /**
   * From a tap only. Restarts the radio. The link goes with it: the radio reboots instead of
   * answering, and a Bluetooth or USB radio the browser already granted is reconnected by
   * `#onLinkLost` once it is back.
   */
  async rebootRadio() {
    await this.#radioSession().reboot()
  }

  // MARK: The radio's tables

  async #syncContacts() {
    if (!this.session) return
    const contacts = await this.session.getContacts()
    this.radioContacts = (contacts ?? []).map((c) => ({
      publicKey: c.publicKey,
      name: c.advertisedName ?? c.name ?? '',
      latitude: c.latitude ?? 0,
      longitude: c.longitude ?? 0,
      // Whole seconds, 0 for never: the shape `WeatherBot.fromContact` reads (the phone's ContactDTO).
      lastAdvertTimestamp: c.lastAdvertisement ? Math.floor(c.lastAdvertisement / 1000) : 0,
      type: c.type ?? null,
    }))
    this.contacts = HeardBots.merge(this.radioContacts, this.heardBots)
    this.log(`contacts: the radio lists ${this.radioContacts.length}`)
    this.#changed()
  }

  /**
   * An advert from a node the radio did not keep (it adds contacts by hand, or its list is full).
   * A weather bot among them is remembered here, so it can be named and asked; the radio's own
   * list is left alone (`HeardBots.js`).
   */
  #heardAdvert(contact) {
    const row = HeardBots.fromAdvert(contact, { now: Date.now() })
    if (row == null) return
    this.heardBots = HeardBots.upsert(this.heardBots, row)
    this.kv.set(HeardBots.storageKey, HeardBots.toStored(this.heardBots)).catch(() => {})
    this.contacts = HeardBots.merge(this.radioContacts, this.heardBots)
    this.log(`heard ${row.name} advertise; the radio did not keep it, so it is kept here`)
    this.#changed()
  }

  async #syncChannels() {
    const session = this.session
    if (!session) return
    const channels = []
    for (let index = 0; index < this.maxChannels; index++) {
      if (this.session !== session) return
      let info = null
      try { info = await session.getChannel({ index }) } catch { break }     // past the radio's last slot
      const secret = info?.secret
      const isEmpty = !info || (!info.name && (!secret || secret.every((b) => b === 0)))
      if (!isEmpty) channels.push({ index, name: info.name ?? '', secret })
    }
    this.channels = channels
    this.weatherSlot = await this.#weatherSlotFromTable()
    this.isChannelSyncDone = true
    this.#changed()
  }

  async #weatherSlotFromTable() {
    const secret = await WeatherChannel.secret()
    const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => v === b[i])
    return (this.channels.find((c) => same(c.secret, secret)) ?? this.channels.find((c) => c.name === WeatherChannel.name))?.index ?? null
  }

  /** Whether the radio's table holds `#meshwx`; null until the table has been read. */
  get hasWeatherChannel() {
    if (!this.isChannelSyncDone) return null
    return this.weatherSlot != null
  }

  // MARK: Session events

  #onSessionEvent(event) {
    this.radioEvents[event.kind] = (this.radioEvents[event.kind] ?? 0) + 1
    if (event.kind === 'parseFailure') this.log(`could not read a frame from the radio: ${event.reason ?? event.message ?? ''}`)
    switch (event.kind) {
      case 'connectionStateChanged':
        if (event.value === 'disconnected' && this.state !== 'disconnected') this.#onLinkLost()
        break
      case 'contactMessage':
        this.#keepOtherMessage({ kind: 'contact', from: hexPrefix(event.message?.senderPublicKeyPrefix), text: event.message?.text ?? '', receivedAt: Date.now() })
        break
      case 'channelMessage':
        this.#keepOtherMessage({ kind: 'channel', from: this.#channelName(event.message?.channelIndex), text: event.message?.text ?? '', receivedAt: Date.now() })
        break
      case 'newContact':
        this.#heardAdvert(event.value)
        // falls through: the list is re-read as well, in case the radio did keep it
      case 'advertisement':
      case 'pathUpdate':
        clearTimeout(this.contactsTimer)
        this.contactsTimer = setTimeout(() => this.#syncContacts().catch(() => {}), 2000)
        break
      default:
    }
  }

  #channelName(index) {
    const channel = this.channels.find((c) => c.index === index)
    return channel?.name || (index === 0 ? 'Public' : `Channel ${index ?? '?'}`)
  }

  #keepOtherMessage(message) {
    this.otherMessages = [message, ...this.otherMessages].slice(0, OTHER_MESSAGES_LIMIT)
    this.kv.set(OTHER_MESSAGES_KEY, this.otherMessages).catch(() => {})
    this.#changed()
  }

  async clearOtherMessages() {
    this.otherMessages = []
    await this.kv.delete(OTHER_MESSAGES_KEY).catch(() => {})
    this.#changed()
  }

  // MARK: Losing the link, and getting it back

  async #onLinkLost() {
    const kind = this.kind
    const granted = kind === LinkKind.bluetooth ? this.frames?.device ?? null
      : kind === LinkKind.serial ? this.frames?.port ?? null : null
    await this.#teardown()
    this.state = 'disconnected'
    this.kind = null
    this.#changed()
    if (this.userDisconnected || !granted) return
    // A device the user already chose can be reopened without another gesture.
    const delay = RECONNECT_DELAYS_MS[Math.min(this.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
    this.reconnectAttempt += 1
    this.reconnectTimer = setTimeout(() => {
      if (this.state !== 'disconnected' || this.userDisconnected) return
      this.connect(kind, { granted }).catch(() => this.#onRetryFailed(kind, granted))
    }, delay)
  }

  #onRetryFailed(kind, granted) {
    if (this.userDisconnected || this.reconnectAttempt >= 8) return
    const delay = RECONNECT_DELAYS_MS[Math.min(this.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
    this.reconnectAttempt += 1
    this.reconnectTimer = setTimeout(() => {
      if (this.state !== 'disconnected' || this.userDisconnected) return
      this.connect(kind, { granted }).catch(() => this.#onRetryFailed(kind, granted))
    }, delay)
  }

  async #reconnectSerial() {
    try {
      const ports = await navigator.serial?.getPorts?.()
      if (ports?.length === 1) await this.connect(LinkKind.serial, { granted: ports[0] })
    } catch { /* the user connects by hand */ }
  }

  async #reconnectBluetooth() {
    try {
      const devices = await navigator.bluetooth?.getDevices?.()
      if (devices?.length === 1) await this.connect(LinkKind.bluetooth, { granted: devices[0] })
    } catch { /* the user connects by hand */ }
  }

  #lastLink() {
    try { return globalThis.localStorage?.getItem(LAST_LINK_KEY) ?? null } catch { return null }
  }

  #rememberLink(kind) {
    try {
      if (kind) globalThis.localStorage?.setItem(LAST_LINK_KEY, kind)
      else globalThis.localStorage?.removeItem(LAST_LINK_KEY)
    } catch { /* private mode */ }
  }

  #changed() {
    for (const fn of [...this.listeners]) fn(this)
  }
}

/** A refusal from this side rather than from the radio; `problems` names the fields. */
function radioParameterError(problems) {
  const error = new Error(`radio parameter out of range: ${problems.map((p) => p.field).join(', ')}`)
  error.problems = problems
  return error
}

function hexPrefix(bytes) {
  if (!bytes?.length) return ''
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 12)
}

export { findWeatherSlot }
