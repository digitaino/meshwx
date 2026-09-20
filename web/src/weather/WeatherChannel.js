// Port of MC1Services/Services/Weather/WeatherChannel.swift (docs/PORTING.md).

/**
 * The `#meshwx` hashtag channel the weather bot broadcasts on (spec §1.1).
 *
 * A hashtag channel's key derives from its name — `SHA256("#meshwx")[0..<16]`, the same
 * derivation `JoinHashtagChannelView` performs — so there is no secret to distribute and
 * the app can offer to add the channel itself. Whether it does is the user's call
 * (docs/MESHWX.md: prompted, never silent).
 *
 * Deviation from the Swift: `secret` is **async**. The only hash the web platform offers is
 * `crypto.subtle.digest`, which returns a promise, so `secret()` and `existingSlot()` are
 * async where Swift's CryptoKit call is synchronous. The digest is computed once and cached,
 * so only the first caller ever waits.
 */

let cachedSecret = null

export const WeatherChannel = {
  /**
   * The channel name, including the hash. Case matters: the key is derived from these
   * exact bytes, so `#MeshWX` would be a different channel with a different key.
   */
  name: '#meshwx',

  /** The 16-byte channel secret, as a `Uint8Array`. */
  async secret() {
    if (cachedSecret != null) return cachedSecret
    cachedSecret = await hashSecret(WeatherChannel.name)
    return cachedSecret
  },

  /**
   * The slot already carrying `#meshwx`, if any.
   *
   * By secret first: the secret is what decrypts the channel, so a slot holding it *is*
   * `#meshwx` whatever it was named when it was added (another app, a typed variant). The name
   * is the fallback for a table whose secret column has not synced.
   */
  async existingSlot({ in: channels }) {
    const secret = await WeatherChannel.secret()
    const bySecret = channels.find((channel) => bytesEqual(channel.secret, secret))
    if (bySecret != null) return bySecret.index
    const byName = channels.find((channel) => channel.name === WeatherChannel.name)
    return byName != null ? byName.index : null
  },

  /**
   * The first unused slot above 0 (slot 0 is the public channel), or null when the radio is
   * full or reports fewer than two slots.
   */
  freeSlot({ in: channels, maxChannels }) {
    if (maxChannels <= 1) return null
    const used = new Set(channels.map((channel) => channel.index))
    for (let index = 1; index < maxChannels; index += 1) {
      if (!used.has(index)) return index
    }
    return null
  }
}

/** The MeshCore hashtag-channel derivation: `SHA256(name)[0..<16]`. */
export async function hashSecret(name) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(name))
  return new Uint8Array(digest).slice(0, 16)
}

function bytesEqual(lhs, rhs) {
  if (lhs == null || rhs == null || lhs.length !== rhs.length) return false
  for (let index = 0; index < lhs.length; index += 1) {
    if (lhs[index] !== rhs[index]) return false
  }
  return true
}
