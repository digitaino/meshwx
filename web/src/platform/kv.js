// A small async key-value store over IndexedDB: `{ get(key), set(key, value), delete(key) }`,
// which is the interface the weather stores take (docs/PORTING.md). Values are structured-cloned,
// so plain JSON state goes in as it is.
//
// Falls back to memory when IndexedDB is missing or refuses to open (a private window with
// storage blocked): the tool still works for the visit and says nothing was kept.

const DB = 'meshwx'
const STORE = 'kv'

export class KeyValueStore {
  constructor({ name = DB } = {}) {
    this.name = name
    this.memory = new Map()
    this.opening = null
    this.persistent = true
  }

  #open() {
    if (this.opening) return this.opening
    this.opening = new Promise((resolve) => {
      if (typeof indexedDB === 'undefined') return resolve(null)
      let request
      try { request = indexedDB.open(this.name, 1) } catch { return resolve(null) }
      request.onupgradeneeded = () => request.result.createObjectStore(STORE)
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => resolve(null)
      request.onblocked = () => resolve(null)
    }).then((db) => {
      if (!db) this.persistent = false
      return db
    })
    return this.opening
  }

  async #run(mode, body) {
    const db = await this.#open()
    if (!db) return body(null)?.result
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode)
      const request = body(tx.objectStore(STORE))
      tx.oncomplete = () => resolve(request?.result)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  }

  async get(key) {
    const value = await this.#run('readonly', (store) => (store ? store.get(key) : { result: this.memory.get(key) }))
    return value === undefined ? null : value
  }

  async set(key, value) {
    await this.#run('readwrite', (store) => (store ? store.put(value, key) : void this.memory.set(key, value)))
  }

  async delete(key) {
    await this.#run('readwrite', (store) => (store ? store.delete(key) : void this.memory.delete(key)))
  }
}
