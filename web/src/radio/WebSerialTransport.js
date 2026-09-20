// Web Serial over USB, for a MeshCore companion radio plugged into the machine.
//
// **Must be called from a user gesture.** `navigator.serial.requestPort()` opens the
// browser's port chooser and Chromium refuses it outside a click or tap handler, so
// `connect()` has to run synchronously inside one.
//
// A serial link is a byte stream, not a message stream: it splits and coalesces at will, so
// everything goes through the length-prefixed framing of `Frames.js` — `<` + u16 LE length
// out, `>` + u16 LE length in — and the read loop feeds a `WiFiFrameDecoder` that survives
// split chunks, coalesced chunks and garbage between frames.

import { WiFiFrameCodec, WiFiFrameDecoder } from './Frames.js'

/**
 * Baud rate of the MeshCore USB companion. 115200 is the rate the firmware's serial
 * companion opens at; confirm it against the radio on the first on-device session
 * (nothing in the Swift library pins it — iOS has no USB serial path).
 */
export const DEFAULT_BAUD_RATE = 115200

export class WebSerialTransport {
  #port = null
  #reader = null
  #writer = null
  #decoder = new WiFiFrameDecoder()
  #subscribers = new Set()
  #connected = false
  #readLoop = null

  /** Called when the link drops without `disconnect()` being asked for. */
  onDisconnect = null

  /**
   * @param {object} [options]
   * @param {number} [options.baudRate]
   * @param {object} [options.port] an already-granted `SerialPort`, e.g. from
   *   `navigator.serial.getPorts()`. With one, `connect()` needs no chooser and therefore no
   *   user gesture.
   * @param {object[]} [options.filters] USB vendor/product filters for the chooser
   */
  constructor({ baudRate = DEFAULT_BAUD_RATE, port = null, filters = [] } = {}) {
    this.baudRate = baudRate
    this.#port = port
    this.filters = filters
  }

  /** The chosen `SerialPort`, so a dropped link can be reopened without another chooser. */
  get port() {
    return this.#port
  }

  /** Whether this browser has Web Serial at all. */
  static isSupported() {
    return typeof navigator !== 'undefined' && navigator.serial != null
  }

  get isConnected() {
    return this.#connected
  }

  /** Picks a port (unless one was supplied), opens it and starts the read loop. */
  async connect() {
    if (!WebSerialTransport.isSupported()) {
      throw new Error('Web Serial is not available in this browser')
    }

    if (this.#port == null) {
      this.#port = await navigator.serial.requestPort(
        this.filters.length > 0 ? { filters: this.filters } : {},
      )
    }

    await this.#port.open({ baudRate: this.baudRate })
    this.#decoder.reset()
    this.#writer = this.#port.writable.getWriter()
    this.#reader = this.#port.readable.getReader()
    this.#connected = true
    this.#readLoop = this.#runReadLoop()
  }

  async disconnect() {
    const wasConnected = this.#connected
    this.#connected = false
    try {
      await this.#reader?.cancel()
    } catch {
      // The stream may already be closed.
    }
    try {
      this.#reader?.releaseLock()
    } catch {
      // Already released.
    }
    try {
      await this.#writer?.close()
    } catch {
      try {
        this.#writer?.releaseLock()
      } catch {
        // Already released.
      }
    }
    if (wasConnected) await this.#readLoop?.catch(() => {})
    this.#reader = null
    this.#writer = null
    this.#readLoop = null
    try {
      await this.#port?.close()
    } catch {
      // Already closed.
    }
  }

  /** Subscribes to incoming frames (payloads, unframed). Returns the unsubscribe function. */
  subscribe(fn) {
    this.#subscribers.add(fn)
    return () => this.#subscribers.delete(fn)
  }

  /** Writes one command payload, length-prefixed. */
  async send(frame) {
    if (!this.#connected || this.#writer == null) {
      throw new Error('WebSerialTransport: not connected')
    }
    await this.#writer.write(WiFiFrameCodec.encode(frame))
  }

  async #runReadLoop() {
    try {
      for (;;) {
        const { value, done } = await this.#reader.read()
        if (done) break
        if (value == null || value.length === 0) continue
        for (const payload of this.#decoder.decode(new Uint8Array(value))) {
          for (const subscriber of [...this.#subscribers]) subscriber(payload)
        }
      }
    } catch {
      // A read error is a lost link; reported below like any other loss.
    } finally {
      if (this.#connected) {
        this.#connected = false
        this.onDisconnect?.()
      }
    }
  }
}
