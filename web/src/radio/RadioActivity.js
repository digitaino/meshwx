// What a tally of session events says the radio has taken in off the air.
//
// Web only, and small on purpose. `RadioConnection` counts every event kind the session emits as
// bring-up diagnostics; most of those kinds are this page's own commands coming back (`ok`,
// `selfInfo`, `currentTime`). The question a radio settings screen has to answer is different and
// narrower: **has anything at all arrived?** A radio on the wrong four values looks identical to a
// working one — it connects, it names itself, it answers every command — and the only thing that
// tells them apart is silence.
//
// Pure: it reads a plain `{ kind: count }` object and nothing else.

/** Event kinds that mean another node was heard: an advert, a new node, a route that changed. */
const ADVERT_KINDS = Object.freeze(['advertisement', 'newContact', 'pathUpdate'])

/**
 * Event kinds that mean a message arrived. These are the **frame** events, one per frame the radio
 * handed over; the poller re-emits the same message as `contactMessage` / `channelMessage` /
 * `channelData`, and counting both would double every message.
 */
const MESSAGE_KINDS = Object.freeze([
  'contactMessageReceived',
  'channelMessageReceived',
  'channelDataReceived',
])

export const RadioActivity = Object.freeze({
  advertKinds: ADVERT_KINDS,
  messageKinds: MESSAGE_KINDS,

  /**
   * `{ adverts, messages, total }` from a `{ kind: count }` tally.
   *
   * A message the connect drain pulled out of the radio's queue counts: the radio may have heard
   * it before this page arrived, but it did reach this page, which is the proof the screen needs.
   */
  heard(radioEvents = {}) {
    const sum = (kinds) => kinds.reduce((total, kind) => total + (radioEvents?.[kind] ?? 0), 0)
    const adverts = sum(ADVERT_KINDS)
    const messages = sum(MESSAGE_KINDS)
    return { adverts, messages, total: adverts + messages }
  },
})
