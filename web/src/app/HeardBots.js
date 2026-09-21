// Weather radios heard announcing themselves that the radio did not keep.
//
// A companion radio set to add contacts by hand (the stock apps' "auto-add contacts" switched
// off, which is how most people run one) does not save a node it hears advertise: it hands the
// whole contact record to the app in a `newContact` push and forgets it. The phone app files those
// in its own database. This client only ever re-read the radio's list, so on such a radio a bot
// could advertise all day and still be "Weather radio 041D", and every ask stayed blocked with
// "Can't ask until it announces itself" (found by the owner on the first evening with real
// hardware, 2026-09-20).
//
// So the adverts of weather bots, and only those, are kept here, in this browser. Nothing is
// written to the radio: whether a node goes into the radio's own contact list is the owner's
// decision, made in the app they manage their radio with.
import { WeatherBot } from '../weather/index.js'

const hexOf = (bytes) => Array.from(bytes ?? [], (b) => b.toString(16).padStart(2, '0')).join('')
const bytesOf = (hex) => Uint8Array.from((hex.match(/../g) ?? []).map((h) => parseInt(h, 16)))

export const HeardBots = {
  storageKey: 'radio.heardBots',
  /** More bots than one radio will ever hear; the oldest advert goes first. */
  max: 16,

  /**
   * A `newContact` push as a contact row of the shape `RadioConnection.contacts` holds, or null
   * when the node is not a weather bot. `now` stands in for a record with no advert time.
   */
  fromAdvert(contact, { now }) {
    const name = contact?.advertisedName ?? contact?.name ?? ''
    if (contact?.publicKey == null || !WeatherBot.isBotName(name)) return null
    const heard = contact.lastAdvertisement ? Math.floor(contact.lastAdvertisement / 1000) : 0
    return {
      publicKey: contact.publicKey,
      name,
      latitude: contact.latitude ?? 0,
      longitude: contact.longitude ?? 0,
      lastAdvertTimestamp: heard || Math.floor(now / 1000),
      type: contact.type ?? null,
    }
  },

  /** `list` with `row` in it, replacing the same key, newest advert first, at most `max`. */
  upsert(list, row) {
    const id = hexOf(row.publicKey)
    return [row, ...list.filter((r) => hexOf(r.publicKey) !== id)]
      .sort((a, b) => b.lastAdvertTimestamp - a.lastAdvertTimestamp)
      .slice(0, HeardBots.max)
  },

  /** The radio's contacts, then every heard bot the radio does not list. The radio's row wins. */
  merge(radioContacts, heard) {
    const listed = new Set(radioContacts.map((c) => hexOf(c.publicKey)))
    return [...radioContacts, ...heard.filter((r) => !listed.has(hexOf(r.publicKey)))]
  },

  toStored(list) {
    return list.map((r) => ({ ...r, publicKey: hexOf(r.publicKey) }))
  },

  fromStored(stored) {
    if (!Array.isArray(stored)) return []
    return stored
      .filter((r) => typeof r?.publicKey === 'string' && r.publicKey.length === 64 && WeatherBot.isBotName(r.name ?? ''))
      .map((r) => ({ ...r, publicKey: bytesOf(r.publicKey) }))
  },
}
