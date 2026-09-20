// Web Bluetooth over the Nordic UART Service, the same service and characteristics the iOS
// app's `BLEStateMachine` uses (`BLEServiceUUID.swift`).
//
// **Must be called from a user gesture.** `navigator.bluetooth.requestDevice` opens the
// browser's own chooser and Chromium refuses it outside a click or tap handler, so
// `connect()` has to run synchronously inside one — a "Connect a radio" button, never on
// load, never on a timer, never on a reconnect the page decided on by itself.
//
// Framing: one notification is one complete companion frame and one write is one frame; see
// `Frames.js`. The iOS BLE state machine writes each command with a single `writeValue` and
// never chunks, so neither does this.

/** Nordic UART Service. */
export const NORDIC_UART_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
/**
 * TX characteristic — *we* transmit into it (Nordic calls it RX, from the peripheral's
 * side). The iOS naming convention is kept so the two clients read the same.
 */
export const NORDIC_UART_TX_CHARACTERISTIC = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
/** RX characteristic — we receive notifications from it (Nordic's TX). */
export const NORDIC_UART_RX_CHARACTERISTIC = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'

/**
 * Largest single ATT write this transport will attempt. No companion command comes close
 * (`sendChannelData` tops out at 168 bytes), and Chromium already caps a write at 512, so
 * this is a guard rail rather than a chunking threshold — there is no chunking.
 */
export const MAX_WRITE_BYTES = 512

export class WebBluetoothTransport {
  #device = null
  #server = null
  #tx = null
  #rx = null
  #subscribers = new Set()
  #connected = false
  #onCharacteristicValueChanged = null
  #onGattServerDisconnected = null

  /** Called when the link drops without `disconnect()` being asked for. */
  onDisconnect = null

  /**
   * @param {object} [options]
   * @param {string[]} [options.namePrefixes] extra chooser filters. Empty by default: the
   *   iOS app scans by service UUID alone (`scanForPeripherals(withServices:)`), so a radio
   *   with an unexpected name is still offered.
   * @param {object} [options.device] an already-chosen `BluetoothDevice`, e.g. one returned
   *   by `navigator.bluetooth.getDevices()` for a radio the user has permitted before. When
   *   given, `connect()` needs no chooser and therefore no user gesture.
   */
  constructor({ namePrefixes = [], device = null } = {}) {
    this.namePrefixes = namePrefixes
    this.#device = device
  }

  /** Whether this browser has Web Bluetooth at all. */
  static isSupported() {
    return typeof navigator !== 'undefined' && navigator.bluetooth != null
  }

  get isConnected() {
    return this.#connected
  }

  /** The chosen `BluetoothDevice`, so a dropped link can be reopened without another chooser. */
  get device() {
    return this.#device
  }

  /** The chosen device's name, once one has been chosen. */
  get deviceName() {
    return this.#device?.name ?? null
  }

  /**
   * Picks a radio (unless one was supplied), connects GATT, finds the UART service and
   * enables notifications. Call from a user gesture.
   */
  async connect() {
    if (!WebBluetoothTransport.isSupported()) {
      throw new Error('Web Bluetooth is not available in this browser')
    }

    if (this.#device == null) {
      const filters = [{ services: [NORDIC_UART_SERVICE] }]
      for (const namePrefix of this.namePrefixes) filters.push({ namePrefix })
      this.#device = await navigator.bluetooth.requestDevice({
        filters,
        optionalServices: [NORDIC_UART_SERVICE],
      })
    }

    this.#onGattServerDisconnected = () => this.#handleGattDisconnection()
    this.#device.addEventListener('gattserverdisconnected', this.#onGattServerDisconnected)

    this.#server = await this.#device.gatt.connect()
    const service = await this.#server.getPrimaryService(NORDIC_UART_SERVICE)
    this.#tx = await service.getCharacteristic(NORDIC_UART_TX_CHARACTERISTIC)
    this.#rx = await service.getCharacteristic(NORDIC_UART_RX_CHARACTERISTIC)

    this.#onCharacteristicValueChanged = (event) => {
      const view = event.target.value
      if (view == null) return
      // One notification is one frame; copy it out of the shared buffer.
      const frame = new Uint8Array(view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength))
      if (frame.length === 0) return
      for (const subscriber of [...this.#subscribers]) subscriber(frame)
    }
    this.#rx.addEventListener('characteristicvaluechanged', this.#onCharacteristicValueChanged)
    await this.#rx.startNotifications()

    this.#connected = true
  }

  async disconnect() {
    this.#connected = false
    if (this.#rx != null && this.#onCharacteristicValueChanged != null) {
      this.#rx.removeEventListener('characteristicvaluechanged', this.#onCharacteristicValueChanged)
      try {
        await this.#rx.stopNotifications()
      } catch {
        // The link may already be gone; nothing to stop.
      }
    }
    if (this.#device != null && this.#onGattServerDisconnected != null) {
      this.#device.removeEventListener('gattserverdisconnected', this.#onGattServerDisconnected)
    }
    this.#onCharacteristicValueChanged = null
    this.#onGattServerDisconnected = null
    try {
      this.#device?.gatt?.disconnect()
    } catch {
      // Already disconnected.
    }
    this.#server = null
    this.#tx = null
    this.#rx = null
  }

  /** Subscribes to incoming frames. Returns the unsubscribe function. */
  subscribe(fn) {
    this.#subscribers.add(fn)
    return () => this.#subscribers.delete(fn)
  }

  /** Writes one command payload as one ATT write. */
  async send(frame) {
    if (!this.#connected || this.#tx == null) {
      throw new Error('WebBluetoothTransport: not connected')
    }
    if (frame.length > MAX_WRITE_BYTES) {
      throw new Error(
        `WebBluetoothTransport: frame of ${frame.length} bytes exceeds the ${MAX_WRITE_BYTES}-byte write limit`,
      )
    }
    // Acknowledged write, matching the iOS default path. `writeValueWithResponse` is the
    // current name; `writeValue` is the older one some Chromium builds still expose.
    if (typeof this.#tx.writeValueWithResponse === 'function') {
      await this.#tx.writeValueWithResponse(frame)
    } else {
      await this.#tx.writeValue(frame)
    }
  }

  #handleGattDisconnection() {
    if (!this.#connected) return
    this.#connected = false
    this.onDisconnect?.()
  }
}
