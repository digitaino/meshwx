// A `WeatherTransport` that speaks to a real MeshWX bot over its debug bridge instead of over a
// radio: the browser becomes a live client of the bot, with no Bluetooth and no mesh.
//
// Port of the iOS app's DEBUG-only `RemoteBotWeatherTransport`. Development only: the bridge is
// the bot operator's tool, gated by a token that `tools/dev-server.mjs` adds server-side, so the
// page itself only ever talks to its own origin (`/api/bridge/...`).
//
// The bot's side is `meshcore_weather/portal/routes/bridge.py`:
//
// - `GET  /api/bridge/stream?since=<cursor>`: SSE, one JSON frame per datagram the bot transmits.
//   Each frame becomes the channel datagram the service expects, on `channelIndex`.
// - `POST /api/bridge/request`: `{"text": ">o KAUS"}` run through the same handler a DM takes, so
//   the bot's own spacing and hourly budget apply. **Its answer goes on the real air** and
//   therefore also arrives on the stream.
//
// What it fakes, and why that is honest enough for development:
//
// - **The slot.** The datagrams claim `channelIndex` and `channelSecret({ at })` reports the
//   `#meshwx` secret for it, so the service's slot check passes without a radio.
// - **The delivery confirmation.** The bridge's "accepted" stands in for the radio's ACK push.
//   A request the bot refused gets none, so it times out the way an unanswered one does.
// - **SNR** is a plausible constant: there is no radio to measure.

import { WeatherBot, WeatherChannel } from '../weather/index.js'

const FIRST_RETRY_MS = 500
const MAX_RETRY_MS = 30_000
const MESHWX_DATA_TYPE = 0xff10

export class RemoteBotWeatherTransport {
  /** The slot the bridge's datagrams claim to have arrived on. */
  static channelIndex = 1
  static syntheticSNR = 6.5

  /**
   * @param {object} options
   * @param {string} [options.baseURL] the origin serving `/api/bridge` (default: this page's)
   * @param {string} [options.clientID] how this client names itself; the bot's per-sender
   *   spacing is keyed on it
   * @param {typeof fetch} [options.fetch]
   * @param {(message: string) => void} [options.log]
   */
  constructor({ baseURL = '', clientID = 'web', fetch: fetchFn, log } = {}) {
    this.baseURL = baseURL.replace(/\/$/, '')
    this.clientID = clientID
    this.fetch = fetchFn ?? ((...a) => globalThis.fetch(...a))
    this.log = log ?? (() => {})
    this.datagramListeners = new Set()
    this.acknowledgementListeners = new Set()
    this.linkListeners = new Set()
    this.cursor = null
    this.link = null
    this.reader = null          // AbortController of the running feed
    this.ackCounter = 0
    this.feedOpen = false
  }

  // MARK: WeatherTransport

  subscribeDatagrams(fn) {
    this.datagramListeners.add(fn)
    this.#startReaderIfNeeded()
    return () => {
      this.datagramListeners.delete(fn)
      if (this.datagramListeners.size === 0) this.#stopReader()
    }
  }

  subscribeAcknowledgements(fn) {
    this.acknowledgementListeners.add(fn)
    return () => this.acknowledgementListeners.delete(fn)
  }

  /** Not part of the Swift protocol: tells the app when the feed opens or drops. */
  subscribeLink(fn) {
    this.linkListeners.add(fn)
    return () => this.linkListeners.delete(fn)
  }

  async resetPath() {}

  async sendRequest({ text }) {
    const code = new Uint8Array(4)
    new DataView(code.buffer).setUint32(0, ++this.ackCounter, true)
    if (await this.#post(text)) for (const fn of this.acknowledgementListeners) fn(code)
    return code
  }

  async sendChannelRequest({ text, botID, seq }) {
    const accepted = await this.#post(text)
    this.log(`bridge took channel request ${text} for bot ${botID} seq ${seq}: accepted=${accepted}`)
  }

  async channelSecret({ at }) {
    return at === RemoteBotWeatherTransport.channelIndex ? WeatherChannel.secret() : null
  }

  async isDrainingBacklog() { return false }

  async linkState() {
    return this.link ?? this.#fetchLink()
  }

  get isFeedOpen() { return this.feedOpen }

  // MARK: Requests

  async #post(text) {
    const response = await this.fetch(`${this.baseURL}/api/bridge/request`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-bridge-client': this.clientID },
      body: JSON.stringify({ text, client: this.clientID }),
    })
    if (!response.ok) throw new Error(`bridge answered ${response.status}`)
    const body = await response.json().catch(() => ({}))
    if (body.accepted !== true) {
      // The bot heard it and chose not to answer. No confirmation: the request times out in the
      // app exactly as one the bot ignored on the air does.
      this.log(`bridge refused ${text}: ${body.outcome ?? 'unknown'}`)
      return false
    }
    this.log(`bridge accepted ${text} (${body.packets ?? 0} packet(s))`)
    return true
  }

  async #fetchLink() {
    if (this.fetchingLink) return this.link
    this.fetchingLink = true
    try {
      const response = await this.fetch(`${this.baseURL}/api/bridge/info`, {
        headers: { 'x-bridge-client': this.clientID },
      })
      if (!response.ok) return this.link
      const body = await response.json()
      const bot = RemoteBotWeatherTransport.bot(body)
      if (bot) this.link = { kind: 'up', bot }
      return this.link
    } catch (error) {
      this.log(`bridge info: ${error.message}`)
      return this.link
    } finally {
      this.fetchingLink = false
    }
  }

  static bot(body) {
    const hex = typeof body?.public_key === 'string' ? body.public_key : ''
    if (!body?.name || hex.length < 4) return null
    const publicKey = new Uint8Array(hex.match(/../g).map((b) => parseInt(b, 16)))
    return WeatherBot.make({ publicKey, name: body.name, latitude: 0, longitude: 0, lastAdvert: null })
  }

  // MARK: The feed

  #startReaderIfNeeded() {
    if (this.reader || this.datagramListeners.size === 0) return
    this.reader = new AbortController()
    this.#readLoop(this.reader.signal)
  }

  #stopReader() {
    this.reader?.abort()
    this.reader = null
    this.#setFeedOpen(false)
  }

  #setFeedOpen(open) {
    if (this.feedOpen === open) return
    this.feedOpen = open
    for (const fn of this.linkListeners) fn(open)
  }

  async #readLoop(signal) {
    let retry = FIRST_RETRY_MS
    while (!signal.aborted) {
      try {
        await this.#readOnce(signal, () => { retry = FIRST_RETRY_MS })
        this.log('bridge feed closed by the bot; reconnecting')
      } catch (error) {
        if (signal.aborted) return
        this.log(`bridge feed: ${error.message}`)
      }
      this.#setFeedOpen(false)
      if (signal.aborted) return
      await new Promise((resolve) => setTimeout(resolve, retry))
      retry = Math.min(retry * 2, MAX_RETRY_MS)
    }
  }

  async #readOnce(signal, onFrame) {
    const since = this.cursor == null ? '' : `?since=${this.cursor}`
    const response = await this.fetch(`${this.baseURL}/api/bridge/stream${since}`, {
      headers: { accept: 'text/event-stream', 'x-bridge-client': this.clientID },
      signal,
    })
    if (!response.ok || !response.body) throw new Error(`stream answered ${response.status}`)
    // Who the feed belongs to, re-read on every connect: a bridge restarted against another bot
    // would otherwise keep the name the tool learned at the first one.
    this.link = null
    await this.#fetchLink()
    this.#setFeedOpen(true)

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffered = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      buffered += value
      let newline
      while ((newline = buffered.indexOf('\n')) >= 0) {
        const line = buffered.slice(0, newline).replace(/\r$/, '')
        buffered = buffered.slice(newline + 1)
        if (!line.startsWith('data:')) continue        // ": ping" and blank lines
        let frame
        try { frame = JSON.parse(line.slice(5).trim()) } catch { continue }
        onFrame()
        if (frame.hello === true) continue
        this.#deliver(frame)
      }
    }
  }

  #deliver(frame) {
    if (typeof frame.hex !== 'string' || frame.hex.length % 2) return
    if (Number.isInteger(frame.cursor)) this.cursor = frame.cursor
    const data = new Uint8Array((frame.hex.match(/../g) ?? []).map((b) => parseInt(b, 16)))
    const datagram = {
      channelIndex: RemoteBotWeatherTransport.channelIndex,
      pathLength: 0xff,
      dataType: Number.isInteger(frame.data_type) ? frame.data_type & 0xffff : MESHWX_DATA_TYPE,
      data,
      snr: RemoteBotWeatherTransport.syntheticSNR,
    }
    for (const fn of this.datagramListeners) fn(datagram)
  }
}
