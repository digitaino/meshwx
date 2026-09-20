// Port of MC1ServicesTests/Weather/WeatherChannelTests.swift (docs/PORTING.md).

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { createHash } from 'node:crypto'

import { WeatherChannel, hashSecret } from '../src/weather/index.js'

/** A `ChannelDTO` as the radio layer hands one over. */
function channel({ index, name, secret = new Uint8Array(16).fill(0xab) }) {
  return { index, name, secret, isEnabled: true, lastMessageDate: null, unreadCount: 0 }
}

describe('WeatherChannel', () => {
  // Spec §1.1: the key derives from the name like any MeshCore hashtag channel —
  // `sha256("#meshwx")[0..<16]`, hash taken over the name *with* its `#`.
  it('the secret is the first sixteen bytes of sha256 over the hashtag name', async () => {
    const expected = new Uint8Array(createHash('sha256').update('#meshwx').digest()).slice(0, 16)
    assert.deepStrictEqual(await WeatherChannel.secret(), expected)
    assert.deepStrictEqual(await WeatherChannel.secret(), await hashSecret('#meshwx'))
    assert.equal((await WeatherChannel.secret()).length, 16)
  })

  it('an existing slot is found by exact name only', async () => {
    const channels = [channel({ index: 0, name: 'Public' }), channel({ index: 3, name: '#meshwx' })]
    assert.equal(await WeatherChannel.existingSlot({ in: channels }), 3)
    // A differently-cased name hashes to a different key and is a different channel.
    assert.equal(await WeatherChannel.existingSlot({ in: [channel({ index: 2, name: '#MeshWX' })] }), null)
    assert.equal(await WeatherChannel.existingSlot({ in: [] }), null)
  })

  // The secret is what decrypts the channel; a slot holding it is #meshwx whatever it is
  // called in the app's table.
  it('a slot is found by the meshwx secret whatever its name', async () => {
    const renamed = channel({ index: 31, name: 'Weather', secret: await WeatherChannel.secret() })
    assert.equal(
      await WeatherChannel.existingSlot({
        in: [channel({ index: 1, name: '#meshwx-discover' }), renamed]
      }),
      31
    )
  })

  it('the free slot skips the public channel and every used index', () => {
    const channels = [
      channel({ index: 0, name: 'Public' }),
      channel({ index: 1, name: '#austin' }),
      channel({ index: 3, name: 'x' })
    ]
    assert.equal(WeatherChannel.freeSlot({ in: channels, maxChannels: 8 }), 2)
    assert.equal(WeatherChannel.freeSlot({ in: [], maxChannels: 8 }), 1)
  })

  it('a full radio or a single-slot radio has no free slot', () => {
    const full = Array.from({ length: 4 }, (_, index) => channel({ index, name: `c${index}` }))
    assert.equal(WeatherChannel.freeSlot({ in: full, maxChannels: 4 }), null)
    assert.equal(WeatherChannel.freeSlot({ in: [], maxChannels: 1 }), null)
    assert.equal(WeatherChannel.freeSlot({ in: [], maxChannels: 0 }), null)
  })
})
