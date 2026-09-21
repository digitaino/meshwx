// The session: the port of `MeshCore/Session/MeshCoreSession*.swift`, narrowed to what a
// browser weather client needs.
//
// It owns a transport, serialises commands (one exchange in flight, exactly as the Swift
// `RequestResponseSerializer` does and for the same reason: the dispatcher broadcasts with
// no per-command correlation, so two exchanges at once consume each other's responses),
// routes pushes to subscribers, performs the connect handshake, and drains the radio's
// message queue.
//
// `AsyncStream` becomes `subscribe(fn) → unsubscribe` (docs/PORTING.md §3). Every event is
// `{ kind, … }`; see the event table in `index.js`.

import { bytesEqual } from './Bytes.js'
import { CHANNEL_DATAGRAM_FIRMWARE_VERSION } from './PacketCodes.js'
import { PacketBuilder } from './PacketBuilder.js'
import { PacketParser } from './PacketParser.js'

/** Errors thrown by the session, with `kind` set to the Swift `MeshCoreError` case name. */
export class MeshCoreError extends Error {
  constructor(kind, message, fields = {}) {
    super(message)
    this.name = 'MeshCoreError'
    this.kind = kind
    Object.assign(this, fields)
  }

  static timeout(what = 'command') {
    return new MeshCoreError('timeout', `MeshCore ${what} timed out`)
  }

  /** The radio answered `RESP_CODE_ERR`; `code` is the firmware's `ERR_CODE_*` byte. */
  static deviceError(code) {
    return new MeshCoreError('deviceError', `MeshCore device error ${code}`, { code })
  }

  static notConnected() {
    return new MeshCoreError('notConnected', 'MeshCore transport is not connected')
  }

  static invalidInput(message) {
    return new MeshCoreError('invalidInput', message)
  }

  static sessionNotStarted() {
    return new MeshCoreError('sessionNotStarted', 'MeshCore session has not been started')
  }
}

/** Session configuration; the Swift defaults, in seconds as in Swift (`TimeInterval`). */
export const SessionConfiguration = Object.freeze({
  make({
    defaultTimeout = 5.0,
    // Truncated to 5 bytes on the wire, so this is "MeshC" — the same five bytes the Swift
    // client sends, which is what the radio displays.
    clientIdentifier = 'MeshCore-Web',
    contactStreamInactivityTimeout = 15.0,
    contactStreamHardTimeout = 180.0,
  } = {}) {
    return {
      defaultTimeout,
      clientIdentifier,
      contactStreamInactivityTimeout,
      contactStreamHardTimeout,
    }
  },
})

/** Connection states this session reports. */
export const ConnectionState = Object.freeze({
  disconnected: 'disconnected',
  connecting: 'connecting',
  connected: 'connected',
})

/** How long the app's clock may differ from the radio's before the radio is corrected.
 *
 *  Setting the radio clock backward makes its request timestamps fall below the
 *  `last_timestamp` remote repeaters recorded for it, and their replay protection then
 *  silently drops every packet until the clock re-passes the stored value. A tight
 *  tolerance keeps each backward step, and therefore each deaf window, no longer than the
 *  tolerance itself. (`ConnectionManager.deviceClockDriftTolerance`.) */
export const DEVICE_CLOCK_DRIFT_TOLERANCE_SECONDS = 5

export class MeshCoreSession {
  #transport
  #configuration
  #subscribers = new Set()
  #unsubscribeTransport = null

  #state = ConnectionState.disconnected
  #selfInfo = null
  #deviceInfo = null
  #deviceTime = null
  #running = false

  // One exchange at a time. `#queue` is the serializer; `#waiter` is the exchange holding it.
  #queue = Promise.resolve()
  #waiter = null

  // Message poller.
  #drainTask = null
  #drainRequested = false
  #backlogDrainPending = false
  #drainingBacklog = false

  /**
   * @param {object} transport a `connect/disconnect/send/subscribe/onDisconnect` transport
   * @param {object} [options]
   * @param {object} [options.configuration] see `SessionConfiguration.make`
   * @param {() => number} [options.now] injected clock, milliseconds; tests override it
   */
  constructor(transport, { configuration = SessionConfiguration.make(), now = Date.now } = {}) {
    this.#transport = transport
    this.#configuration = configuration
    this.now = now
  }

  // MARK: - State

  /** `disconnected` | `connecting` | `connected`. */
  get state() {
    return this.#state
  }

  /** The radio's own info after `start()`: public key, name, radio parameters. */
  get selfInfo() {
    return this.#selfInfo
  }

  /** The radio's capabilities after `start()`: firmware version code, model, maxima. */
  get deviceInfo() {
    return this.#deviceInfo
  }

  /** The last device time the radio reported, in milliseconds, or `null`. */
  get deviceTime() {
    return this.#deviceTime
  }

  /** The transport this session owns. */
  get transport() {
    return this.#transport
  }

  /**
   * Whether this radio both delivers and sends channel datagrams: firmware version code 11
   * (MeshCore v1.15.0) or newer. Older firmware drops a `GRP_DATA` packet silently — no
   * error, no event — which is why the weather layer checks this before promising anything.
   */
  get supportsChannelDatagrams() {
    return (this.#deviceInfo?.firmwareVersion ?? 0) >= CHANNEL_DATAGRAM_FIRMWARE_VERSION
  }

  /**
   * Whether the radio's message queue — what it held while the browser was away — is being
   * drained right now. A message delivered meanwhile is backlog: it can be hours old, and it
   * says nothing about whether its sender is in range now.
   *
   * True for the connect drain only, exactly as the Swift `MessagePollingService` defines it
   * (`isDrainingBacklog == isPolling`, and `pollAllMessages()` runs at connect and on
   * resync). A drain started by a messages-waiting push is live traffic, not backlog.
   */
  get isDrainingBacklog() {
    return this.#drainingBacklog
  }

  // MARK: - Events

  /**
   * Subscribes to every event. Returns the unsubscribe function.
   *
   * A subscriber that throws does not break the receive loop or any other subscriber.
   */
  subscribe(fn) {
    this.#subscribers.add(fn)
    return () => this.#subscribers.delete(fn)
  }

  /**
   * Waits for the first event `matching` accepts, or `null` on timeout. Not serialised: use
   * it to wait on a push (a delivery ACK) while commands keep flowing.
   */
  async waitForEvent({ matching, timeout = null }) {
    const ms = (timeout ?? this.#configuration.defaultTimeout) * 1000
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        unsubscribe()
        resolve(null)
      }, ms)
      timer.unref?.()
      const unsubscribe = this.subscribe((event) => {
        if (!matching(event)) return
        clearTimeout(timer)
        unsubscribe()
        resolve(event)
      })
    })
  }

  // MARK: - Lifecycle

  /**
   * Connects the transport and runs the handshake the iOS app runs:
   *
   *   1. transport connect
   *   2. `appStart` → self info (public key, name, radio parameters)
   *   3. `deviceQuery` → firmware version code, model, max contacts/channels
   *   4. the clock, but only when it has drifted (see `syncDeviceTimeIfNeeded`)
   *   5. the connect drain: `getMessage` until the radio says no more
   *
   * A failed `appStart` unwinds the half-started session so a retry runs the full handshake
   * instead of no-opping.
   */
  async start() {
    if (this.#running) return
    this.#setState(ConnectionState.connecting)

    this.#unsubscribeTransport = this.#transport.subscribe((frame) => this.#handleReceivedData(frame))
    this.#transport.onDisconnect = () => this.#handleTransportLoss()

    try {
      await this.#transport.connect()
    } catch (error) {
      this.#unwindTransportSubscription()
      this.#setState(ConnectionState.disconnected)
      throw error
    }

    this.#running = true
    this.#setState(ConnectionState.connected)

    try {
      this.#selfInfo = await this.sendAppStart()
      this.#deviceInfo = await this.queryDevice()
    } catch (error) {
      this.#running = false
      this.#unwindTransportSubscription()
      await this.#transport.disconnect().catch(() => {})
      this.#setState(ConnectionState.disconnected)
      throw error
    }

    await this.syncDeviceTimeIfNeeded()
    await this.#requestDrain({ backlog: true })
  }

  /** Alias for `start()`: the browser layers speak of connecting a radio. */
  async connect() {
    return this.start()
  }

  /** Stops the session and disconnects the transport. Safe to call more than once. */
  async stop({ disconnectTransport = true } = {}) {
    this.#running = false
    this.#drainRequested = false
    this.#failWaiter(MeshCoreError.notConnected())
    this.#unwindTransportSubscription()
    if (disconnectTransport) await this.#transport.disconnect().catch(() => {})
    this.#setState(ConnectionState.disconnected)
  }

  /** Alias for `stop()`. */
  async disconnect() {
    return this.stop()
  }

  // MARK: - Device commands

  /** `appStart` → the radio's own info. */
  async sendAppStart() {
    const data = PacketBuilder.appStart({ clientId: this.#configuration.clientIdentifier })
    return this.#sendAndWait(data, (event) => (event.kind === 'selfInfo' ? event.value : null))
  }

  /** `deviceQuery` → firmware version code, model, max contacts/channels. */
  async queryDevice() {
    return this.#sendAndWait(PacketBuilder.deviceQuery(), (event) =>
      event.kind === 'deviceInfo' ? event.value : null,
    )
  }

  /** Battery millivolts and storage use. */
  async getBattery() {
    return this.#sendAndWait(PacketBuilder.getBattery(), (event) =>
      event.kind === 'battery' ? event.value : null,
    )
  }

  /** The radio's clock, in milliseconds since the epoch. */
  async getTime() {
    return this.#sendAndWait(PacketBuilder.getTime(), (event) =>
      event.kind === 'currentTime' ? event.value : null,
    )
  }

  /** Sets the radio's clock. `date` is milliseconds since the epoch. */
  async setTime(date) {
    await this.#sendSimpleCommand(PacketBuilder.setTime(date))
  }

  /**
   * Corrects the radio's clock, but only when it has drifted past the tolerance — the same
   * condition `ConnectionManager.syncDeviceTimeIfNeeded()` applies, and for the reason
   * recorded there: every backward step blinds the radio to repeaters that remember a later
   * timestamp for it. Failures are reported as an event, never thrown: a radio whose clock
   * could not be read is still usable.
   */
  async syncDeviceTimeIfNeeded() {
    try {
      const deviceTime = await this.getTime()
      const driftSeconds = Math.abs(deviceTime - this.now()) / 1000
      if (driftSeconds > DEVICE_CLOCK_DRIFT_TOLERANCE_SECONDS) {
        await this.setTime(this.now())
        this.#dispatch({ kind: 'deviceTimeSynced', driftSeconds })
      }
    } catch (error) {
      this.#dispatch({ kind: 'deviceTimeSyncFailed', error })
    }
  }

  // MARK: - Device configuration

  /**
   * Re-reads the radio's own info. `appStart` is the only command that returns it, and the
   * session caches what comes back (`#dispatch`), so after a write this is how a screen comes to
   * show what the radio now says rather than what was typed into it.
   */
  async refreshSelfInfo() {
    return this.sendAppStart()
  }

  /** Sets the advertised name. Truncated to 31 UTF-8 bytes by the builder. */
  async setName(name) {
    await this.#sendSimpleCommand(PacketBuilder.setName(name))
  }

  /**
   * Sets the four LoRa parameters. `frequency` is MHz, `bandwidth` kHz.
   *
   * Nothing is validated here, as in the Swift: the firmware answers
   * `ERR_CODE_ILLEGAL_ARGUMENT` for a value outside its range and this throws that error, and the
   * caller checks first (`RadioParameters.validateRadio`) so a person sees the problem before the
   * radio does.
   */
  async setRadio({ frequency, bandwidth, spreadingFactor, codingRate, clientRepeat = null }) {
    await this.#sendSimpleCommand(
      PacketBuilder.setRadio({ frequency, bandwidth, spreadingFactor, codingRate, clientRepeat }),
    )
  }

  /** Sets transmit power in dBm. The radio's own `maxTxPower` is the ceiling. */
  async setTxPower(power) {
    await this.#sendSimpleCommand(PacketBuilder.setTxPower(power))
  }

  /** Sets the position the radio carries in its adverts, in degrees. */
  async setCoordinates({ latitude, longitude }) {
    await this.#sendSimpleCommand(PacketBuilder.setCoordinates({ latitude, longitude }))
  }

  /**
   * Writes all of the "other params" at once. There is no partial form of the command, so every
   * field goes out on every call; prefer `setManualAddContacts`, which reads the current ones
   * first.
   */
  async setOtherParams({
    manualAddContacts,
    telemetryModeEnvironment = 0,
    telemetryModeLocation = 0,
    telemetryModeBase = 0,
    advertisementLocationPolicy = 0,
    multiAcks = null,
  }) {
    await this.#sendSimpleCommand(
      PacketBuilder.setOtherParams({
        manualAddContacts,
        telemetryModeEnvironment,
        telemetryModeLocation,
        telemetryModeBase,
        advertisementLocationPolicy,
        multiAcks,
      }),
    )
  }

  /**
   * Whether the radio adds by itself the contacts it hears, preserving every other field of
   * `setOtherParams` as self info reported it.
   *
   * Read, modify, write, then read again — the Swift's `mutateOtherParams`. The read-back is not
   * a nicety: the command has no partial form, so the only proof the other fields survived is
   * what the radio says afterwards.
   */
  async setManualAddContacts(enabled) {
    const current = this.#selfInfo ?? (await this.sendAppStart())
    if (current == null) throw MeshCoreError.sessionNotStarted()
    await this.setOtherParams({
      manualAddContacts: enabled,
      telemetryModeEnvironment: current.telemetryModeEnvironment ?? 0,
      telemetryModeLocation: current.telemetryModeLocation ?? 0,
      telemetryModeBase: current.telemetryModeBase ?? 0,
      advertisementLocationPolicy: current.advertisementLocationPolicy ?? 0,
      multiAcks: current.multiAcks ?? null,
    })
    return this.refreshSelfInfo()
  }

  /**
   * Broadcasts an advert. `flood` sends it through the mesh; without it the advert is heard by
   * whatever is in direct range and no further.
   */
  async sendAdvertisement({ flood = false } = {}) {
    await this.#sendSimpleCommand(PacketBuilder.sendAdvertisement({ flood }))
  }

  /**
   * Restarts the radio. Nothing is awaited, as in the Swift: the radio reboots instead of
   * answering, so the link drops and this session is finished. A new one has to be started after
   * the radio comes back.
   */
  async reboot() {
    if (!this.#running) throw MeshCoreError.notConnected()
    await this.#transport.send(PacketBuilder.reboot())
  }

  // MARK: - Contacts

  /**
   * The radio's contact list, through the iteration protocol: `contactsStart(count)`, one
   * `contact` per row, then `contactsEnd`. Each row resets the inactivity timer, so a slow
   * list of 200 contacts does not time out while it is still arriving.
   */
  async getContacts({ since = null } = {}) {
    const contacts = []
    await this.#sendAndMatch(PacketBuilder.getContacts({ since }), {
      timeout: this.#configuration.contactStreamHardTimeout,
      inactivityTimeout: this.#configuration.contactStreamInactivityTimeout,
      matcher: (event) => {
        switch (event.kind) {
          case 'contactsStart':
            return { kind: 'progress' }
          case 'contact':
            contacts.push(event.value)
            return { kind: 'progress' }
          case 'contactsEnd':
            return { kind: 'success', value: event.lastModified }
          case 'error':
            return { kind: 'failure', error: MeshCoreError.deviceError(event.code ?? 0) }
          default:
            return null
        }
      },
    })
    return contacts
  }

  /**
   * Forgets the route the radio holds to a public key, so the next message to it goes out by
   * flood. Requires the full 32-byte key, as the firmware command does.
   */
  async resetPath({ publicKey }) {
    if (publicKey?.length !== PacketBuilder.publicKeySize) {
      throw MeshCoreError.invalidInput(
        `Full ${PacketBuilder.publicKeySize}-byte public key required for resetPath`,
      )
    }
    await this.#sendSimpleCommand(PacketBuilder.resetPath({ publicKey }))
  }

  // MARK: - Messaging

  /**
   * Fetches the next message from the radio's queue.
   *
   * Returns `{ kind: 'contactMessage' | 'channelMessage' | 'channelDatagram', value }` or
   * `{ kind: 'noMoreMessages' }`.
   */
  async getMessage({ timeout = null } = {}) {
    return this.#sendAndMatch(PacketBuilder.getMessage(), {
      timeout,
      matcher: (event) => {
        switch (event.kind) {
          case 'contactMessageReceived':
            return { kind: 'success', value: { kind: 'contactMessage', value: event.value } }
          case 'channelMessageReceived':
            return { kind: 'success', value: { kind: 'channelMessage', value: event.value } }
          case 'channelDataReceived':
            return { kind: 'success', value: { kind: 'channelDatagram', value: event.value } }
          case 'noMoreMessages':
            return { kind: 'success', value: { kind: 'noMoreMessages' } }
          case 'error':
            return { kind: 'failure', error: MeshCoreError.deviceError(event.code ?? 0) }
          default:
            return null
        }
      },
    })
  }

  /**
   * Sends a plain-text DM and returns the radio's `messageSent` info, whose `expectedAck` is
   * the four-byte code the delivery ACK push will carry.
   */
  async sendMessage({ to, text, timestamp = this.now(), attempt = 0 }) {
    return this.#sendAndWaitWithError(
      PacketBuilder.sendMessage({ to, text, timestamp, attempt }),
      (event) => (event.kind === 'messageSent' ? event.value : null),
    )
  }

  /** Broadcasts a text message on a channel slot. */
  async sendChannelMessage({ channel, text, timestamp = this.now() }) {
    await this.#sendSimpleCommand(PacketBuilder.sendChannelMessage({ channel, text, timestamp }))
  }

  /**
   * Sends a binary datagram on a channel slot (firmware v11+). Defaults to flood, which is
   * what a Request datagram wants: no stored route can lose it.
   */
  async sendChannelData({
    channelIndex,
    dataType,
    payload,
    pathLength = PacketBuilder.floodPathSentinel,
    pathBytes = new Uint8Array(0),
  }) {
    await this.#sendSimpleCommand(
      PacketBuilder.sendChannelData({ channelIndex, dataType, payload, pathLength, pathBytes }),
    )
  }

  /** Waits for the delivery ACK whose code equals `expectedAck`; `null` on timeout. */
  async waitForAcknowledgement({ expectedAck, timeout = null }) {
    return this.waitForEvent({
      matching: (event) => event.kind === 'acknowledgement' && bytesEqual(event.code, expectedAck),
      timeout,
    })
  }

  // MARK: - Channels

  /**
   * The configuration of one channel slot: index, name, 16-byte secret.
   *
   * Deviation from Swift: an `error` response rejects at once instead of being ignored until
   * the command times out. A radio answers `ERR_CODE_NOT_FOUND` for a slot past its last
   * one, and the weather slot scan reads 40 slots — five seconds of timeout each would be
   * three minutes on an eight-slot radio. Serialisation makes it safe: only one exchange is
   * in flight, so the only error that can arrive in this window is this request's.
   */
  async getChannel({ index }) {
    return this.#sendAndWaitWithError(PacketBuilder.getChannel({ index }), (event) =>
      event.kind === 'channelInfo' && event.value.index === index ? event.value : null,
    )
  }

  /** Writes a channel slot. `secret` is the 16-byte PSK. */
  async setChannel({ index, name, secret }) {
    await this.#sendSimpleCommand(PacketBuilder.setChannel({ index, name, secret }))
  }

  // MARK: - Stats

  async getStatsCore() {
    return this.#sendAndWait(PacketBuilder.getStatsCore(), (event) =>
      event.kind === 'statsCore' ? event.value : null,
    )
  }

  async getStatsRadio() {
    return this.#sendAndWait(PacketBuilder.getStatsRadio(), (event) =>
      event.kind === 'statsRadio' ? event.value : null,
    )
  }

  async getStatsPackets() {
    return this.#sendAndWait(PacketBuilder.getStatsPackets(), (event) =>
      event.kind === 'statsPackets' ? event.value : null,
    )
  }

  // MARK: - Receive path

  #handleReceivedData(frame) {
    if (frame == null || frame.length === 0) return
    this.#dispatch(PacketParser.parse(frame))
  }

  #handleTransportLoss() {
    if (!this.#running) return
    this.#running = false
    this.#failWaiter(MeshCoreError.notConnected())
    this.#setState(ConnectionState.disconnected)
  }

  #setState(state) {
    if (this.#state === state) return
    this.#state = state
    this.#dispatch({ kind: 'connectionStateChanged', value: state })
  }

  #dispatch(event) {
    // Track the state the session keeps, exactly as `trackContactChanges` does.
    if (event.kind === 'selfInfo') this.#selfInfo = event.value
    if (event.kind === 'deviceInfo') this.#deviceInfo = event.value
    if (event.kind === 'currentTime') this.#deviceTime = event.value

    this.#emit(event)

    // A push routed while a command is in flight: the waiter's matcher ignores it, and the
    // drain it asks for waits its turn on the serializer.
    if (event.kind === 'messagesWaiting') this.#requestDrain({ backlog: false })

    const waiter = this.#waiter
    if (waiter == null) return
    let disposition = null
    try {
      disposition = waiter.matcher(event)
    } catch (error) {
      this.#failWaiter(error)
      return
    }
    if (disposition == null) return
    if (disposition.kind === 'progress') {
      waiter.resetInactivity()
      return
    }
    if (disposition.kind === 'failure') {
      this.#failWaiter(disposition.error)
      return
    }
    this.#waiter = null
    waiter.clear()
    waiter.resolve(disposition.value)
  }

  #emit(event) {
    for (const subscriber of [...this.#subscribers]) {
      try {
        subscriber(event)
      } catch {
        // A subscriber's own failure never breaks the receive loop.
      }
    }
  }

  #failWaiter(error) {
    const waiter = this.#waiter
    if (waiter == null) return
    this.#waiter = null
    waiter.clear()
    waiter.reject(error)
  }

  #unwindTransportSubscription() {
    this.#unsubscribeTransport?.()
    this.#unsubscribeTransport = null
    if (this.#transport.onDisconnect != null) this.#transport.onDisconnect = null
  }

  // MARK: - Command serialisation

  /**
   * Runs `operation` with the serializer held. One exchange at a time: the dispatcher has no
   * per-command correlation, so two exchanges in flight can consume each other's responses.
   * Not reentrant — never call a command method from inside another one.
   */
  #serialize(operation) {
    const run = this.#queue.then(operation, operation)
    this.#queue = run.then(
      () => {},
      () => {},
    )
    return run
  }

  /**
   * Sends `data` and resolves with the first response the matcher accepts.
   *
   * The matcher returns `{ kind: 'success', value }`, `{ kind: 'failure', error }`,
   * `{ kind: 'progress' }` (a streaming response made progress: restart the inactivity
   * timer) or `null` to ignore the event. Subscribing happens before the write, so a
   * response cannot arrive before anyone is listening for it.
   */
  #sendAndMatch(data, { matcher, timeout = null, inactivityTimeout = null }) {
    const hardMs = (timeout ?? this.#configuration.defaultTimeout) * 1000
    const idleMs = inactivityTimeout == null ? null : inactivityTimeout * 1000

    return this.#serialize(
      () =>
        new Promise((resolve, reject) => {
          if (this.#transport == null) {
            reject(MeshCoreError.notConnected())
            return
          }

          let hardTimer = null
          let idleTimer = null
          const clear = () => {
            if (hardTimer != null) clearTimeout(hardTimer)
            if (idleTimer != null) clearTimeout(idleTimer)
            hardTimer = null
            idleTimer = null
          }
          const expire = () => {
            this.#waiter = null
            clear()
            reject(MeshCoreError.timeout())
          }

          hardTimer = setTimeout(expire, hardMs)
          hardTimer.unref?.()
          const resetInactivity = () => {
            if (idleMs == null) return
            if (idleTimer != null) clearTimeout(idleTimer)
            idleTimer = setTimeout(expire, idleMs)
            idleTimer.unref?.()
          }
          resetInactivity()

          this.#waiter = { matcher, resolve, reject, clear, resetInactivity }

          Promise.resolve(this.#transport.send(data)).catch((error) => {
            if (this.#waiter?.resolve === resolve) this.#waiter = null
            clear()
            reject(error)
          })
        }),
    )
  }

  /** `#sendAndMatch` for a matcher that returns the value or `null`. */
  #sendAndWait(data, matching, { timeout = null } = {}) {
    return this.#sendAndMatch(data, {
      timeout,
      matcher: (event) => {
        const value = matching(event)
        return value == null ? null : { kind: 'success', value }
      },
    })
  }

  /** `#sendAndWait` that also fails on the radio's `error` response. */
  #sendAndWaitWithError(data, matching, { timeout = null } = {}) {
    return this.#sendAndMatch(data, {
      timeout,
      matcher: (event) => {
        if (event.kind === 'error') {
          return { kind: 'failure', error: MeshCoreError.deviceError(event.code ?? 0) }
        }
        const value = matching(event)
        return value == null ? null : { kind: 'success', value }
      },
    })
  }

  /**
   * Sends a command whose whole answer is `ok`. A bare `ok` only: firmware answers some
   * commands with `ok` plus a u32, and matching those here would let an unrelated reply
   * complete this command.
   */
  #sendSimpleCommand(data) {
    return this.#sendAndWaitWithError(data, (event) =>
      event.kind === 'ok' && event.value == null ? true : null,
    )
  }

  // MARK: - Message poller

  /**
   * Asks for a drain. A drain already running is re-armed rather than doubled, so a burst of
   * messages-waiting pushes costs one extra pass, not one loop each.
   */
  #requestDrain({ backlog }) {
    this.#drainRequested = true
    if (backlog) this.#backlogDrainPending = true
    if (this.#drainTask != null) return this.#drainTask
    this.#drainTask = this.#runDrainLoop().finally(() => {
      this.#drainTask = null
    })
    return this.#drainTask
  }

  async #runDrainLoop() {
    while (this.#drainRequested && this.#running) {
      this.#drainRequested = false
      const backlog = this.#backlogDrainPending
      this.#backlogDrainPending = false
      this.#drainingBacklog = backlog
      try {
        for (;;) {
          if (!this.#running) break
          const result = await this.getMessage()
          if (result.kind === 'noMoreMessages') break
          this.#emitPolledMessage(result, backlog)
        }
      } catch (error) {
        // The Swift auto-fetch loop logs and moves on. A browser has no log the user reads,
        // so the failure is an event; the drain is re-armed by the next push either way.
        this.#emit({ kind: 'pollFailed', error, isDrainingBacklog: backlog })
      } finally {
        this.#drainingBacklog = false
      }
    }
  }

  /**
   * Every message the poller pulls is emitted, whatever it is. A browser tab that is the
   * radio's companion receives the user's chats too, and a chat this client has no screen
   * for must still reach the UI so the user knows it arrived here.
   */
  #emitPolledMessage(result, backlog) {
    switch (result.kind) {
      case 'contactMessage':
        this.#emit({ kind: 'contactMessage', message: result.value, isDrainingBacklog: backlog })
        break
      case 'channelMessage':
        this.#emit({ kind: 'channelMessage', message: result.value, isDrainingBacklog: backlog })
        break
      case 'channelDatagram':
        this.#emit({ kind: 'channelData', datagram: result.value, isDrainingBacklog: backlog })
        break
      default:
        break
    }
  }
}
