// Port of MC1Services/Services/Weather/WeatherStateStore.swift (docs/PORTING.md).

import { WeatherBotState } from './WeatherBotState.js'

/**
 * Where `WeatherService` keeps its per-bot state between launches.
 *
 * JSON rather than a schema: nothing joins weather state, it is a cache of what a bot said, and
 * a schema migration for a cache is a cost with no benefit (docs/MESHWX.md).
 *
 * The interface, duck-typed — a store is any object with these three methods:
 *
 *   load()          -> Promise<{ [botID: string]: WeatherBotState }>
 *   save(states)    -> Promise<void>
 *   modify(body)    -> Promise<void>
 *
 * `modify` loads, applies `body`, and saves, with no other load, save or modify of this store in
 * between: two writers each doing load-then-save would otherwise lose one of the edits. `body`
 * may mutate the object it is handed or return a new one.
 *
 * Deviation from the Swift: the iOS store writes a file itself. A browser cannot, so the
 * persistence is injected (`KeyValueWeatherStateStore`) and the file store has no port.
 */

/** Bumped when the shape changes; an older blob is discarded rather than migrated. */
export const WEATHER_STATE_FORMAT_VERSION = 1

/** The default key a `KeyValueWeatherStateStore` writes under. */
export const WEATHER_STATE_KEY = 'weather.state'

/**
 * The snapshot that is written: `{ version, bots: [...] }`, bots sorted by id so two equal states
 * render identically. Dates inside are milliseconds, as everywhere in this port (PORTING.md §3) —
 * the Swift file writes seconds, so the two files are not interchangeable, which is why this one
 * carries its own version.
 */
export function weatherStateSnapshot(states) {
  return {
    version: WEATHER_STATE_FORMAT_VERSION,
    bots: Object.values(states).slice().sort((lhs, rhs) => lhs.botID - rhs.botID)
  }
}

/** The states a snapshot holds, or an empty map for anything unreadable or of another version. */
export function weatherStateFromSnapshot(snapshot) {
  if (snapshot == null || typeof snapshot !== 'object') return {}
  if (snapshot.version !== WEATHER_STATE_FORMAT_VERSION) return {}
  if (!Array.isArray(snapshot.bots)) return {}
  const states = {}
  for (const bot of snapshot.bots) {
    if (bot == null || bot.botID == null) continue
    const key = String(bot.botID)
    // Two rows for one bot: the first wins, as the Swift's uniquing does.
    if (states[key] == null) states[key] = WeatherBotState.decode(bot)
  }
  return states
}

/**
 * A store that forgets when it is dropped, for tests and previews.
 *
 * It round-trips through JSON on every save, so a value that could not survive persistence
 * (a `Map`, a `Set`, a `BigInt`) fails here rather than on a real phone.
 */
export class InMemoryWeatherStateStore {
  constructor({ states = {} } = {}) {
    this.states = states
    this.saveCount = 0
    this.#queue = Promise.resolve()
  }

  #queue

  async load() {
    return this.states
  }

  async save(states) {
    this.states = weatherStateFromSnapshot(JSON.parse(JSON.stringify(weatherStateSnapshot(states))))
    this.saveCount += 1
  }

  async modify(body) {
    return this.#serialised(async () => {
      const states = await this.load()
      this.states = body(states) ?? states
      this.saveCount += 1
    })
  }

  #serialised(work) {
    const run = this.#queue.then(work, work)
    this.#queue = run.then(() => undefined, () => undefined)
    return run
  }
}

/**
 * The persisted store: one JSON blob under one key of an injected async key-value store.
 *
 * `storage` is `{ get(key), set(key, value), delete(key) }`, each async — the browser app passes
 * an IndexedDB wrapper. `get` may return the object or the JSON text of it; both are read.
 *
 * One instance per `(storage, key)`: `shared` hands every caller — the connection's service and
 * the tool's offline path alike — the same instance, so their reads and writes are serialised
 * instead of interleaved. That is what the Swift actor does for a file.
 */
export class KeyValueWeatherStateStore {
  constructor({ storage, key = WEATHER_STATE_KEY }) {
    this.storage = storage
    this.key = key
    this.#queue = Promise.resolve()
  }

  #queue

  static #instances = new WeakMap()

  /** The one instance for a `(storage, key)` pair. */
  static shared({ storage, key = WEATHER_STATE_KEY }) {
    let byKey = KeyValueWeatherStateStore.#instances.get(storage)
    if (byKey == null) {
      byKey = new Map()
      KeyValueWeatherStateStore.#instances.set(storage, byKey)
    }
    const existing = byKey.get(key)
    if (existing != null) return existing
    const store = new KeyValueWeatherStateStore({ storage, key })
    byKey.set(key, store)
    return store
  }

  async load() {
    return this.#serialised(() => this.#loadUnlocked())
  }

  async save(states) {
    return this.#serialised(() => this.#saveUnlocked(states))
  }

  /**
   * Serialised inside the queue, so nothing else on this instance runs between the load and
   * the save.
   */
  async modify(body) {
    return this.#serialised(async () => {
      const states = await this.#loadUnlocked()
      await this.#saveUnlocked(body(states) ?? states)
    })
  }

  async #loadUnlocked() {
    let raw
    try {
      raw = await this.storage.get(this.key)
    } catch {
      // Unreadable storage starts empty: the next broadcast rebuilds everything within hours.
      return {}
    }
    if (raw == null) return {}
    let snapshot = raw
    if (typeof raw === 'string') {
      try { snapshot = JSON.parse(raw) } catch { return {} }
    }
    return weatherStateFromSnapshot(snapshot)
  }

  async #saveUnlocked(states) {
    if (Object.keys(states).length === 0) {
      // Nothing left to hold: drop the blob rather than leave an empty one behind. A load reads
      // the same either way.
      await this.storage.delete(this.key)
      return
    }
    await this.storage.set(this.key, weatherStateSnapshot(states))
  }

  #serialised(work) {
    const run = this.#queue.then(work, work)
    this.#queue = run.then(() => undefined, () => undefined)
    return run
  }
}
