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
  MeshCoreSession, WebBluetoothTransport, WebSerialTransport,
} from '../radio/index.js'
import { SessionWeatherTransport, findWeatherSlot, addWeatherChannel } from '../link/SessionWeatherTransport.js'
import { RemoteBotWeatherTransport } from '../link/RemoteBotWeatherTransport.js'
import { ReplayWeatherTransport } from '../link/ReplayWeatherTransport.js'
import { WeatherChannel } from '../weather/index.js'

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
    this.isChannelSyncDone = false

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
    this.label = self.name ?? frames.deviceName ?? null
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

  // MARK: The radio's tables

  async #syncContacts() {
    if (!this.session) return
    const contacts = await this.session.getContacts()
    this.contacts = (contacts ?? []).map((c) => ({
      publicKey: c.publicKey,
      name: c.advertisedName ?? c.name ?? '',
      latitude: c.latitude ?? 0,
      longitude: c.longitude ?? 0,
      // Whole seconds, 0 for never: the shape `WeatherBot.fromContact` reads (the phone's ContactDTO).
      lastAdvertTimestamp: c.lastAdvertisement ? Math.floor(c.lastAdvertisement / 1000) : 0,
      type: c.type ?? null,
    }))
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
      case 'advertisement':
      case 'newContact':
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

function hexPrefix(bytes) {
  if (!bytes?.length) return ''
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 12)
}

export { findWeatherSlot }
