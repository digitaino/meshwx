// Weather bots heard advertising on a radio that does not keep new contacts (HeardBots.js).
import test from 'node:test'
import assert from 'node:assert/strict'
import { HeardBots } from '../src/app/HeardBots.js'
import { WeatherBot } from '../src/weather/index.js'

const key = (first, second = 0x04) => Uint8Array.from({ length: 32 }, (_, i) => (i === 0 ? first : i === 1 ? second : i))
const advert = (name, publicKey, at = 1_789_960_000_000) => ({
  publicKey, advertisedName: name, latitude: 30.27, longitude: -97.74, lastAdvertisement: at, type: 1,
})

test('a weather bot heard advertising becomes a contact row', () => {
  const row = HeardBots.fromAdvert(advert('WX-AUS', key(0x1d)), { now: 5_000 })
  assert.equal(row.name, 'WX-AUS')
  assert.equal(row.lastAdvertTimestamp, 1_789_960_000)
  const bot = WeatherBot.fromContact(row)
  assert.equal(WeatherBot.botID(bot), 0x041d)
})

test('anything that is not a weather bot is left to the radio', () => {
  assert.equal(HeardBots.fromAdvert(advert('Rafael mobile', key(0x22)), { now: 0 }), null)
  assert.equal(HeardBots.fromAdvert(advert('WX-', key(0x23)), { now: 0 }), null)
  assert.equal(HeardBots.fromAdvert(null, { now: 0 }), null)
})

test('an advert with no time of its own is dated now', () => {
  const row = HeardBots.fromAdvert(advert('WX-SAT', key(0x30), 0), { now: 1_700_000_000_500 })
  assert.equal(row.lastAdvertTimestamp, 1_700_000_000)
})

test('the same bot heard again replaces its row, and the list is capped', () => {
  let list = []
  list = HeardBots.upsert(list, HeardBots.fromAdvert(advert('WX-AUS', key(0x1d), 1_000_000), { now: 0 }))
  list = HeardBots.upsert(list, HeardBots.fromAdvert(advert('WX-AUS', key(0x1d), 2_000_000), { now: 0 }))
  assert.equal(list.length, 1)
  assert.equal(list[0].lastAdvertTimestamp, 2_000)
  for (let i = 0; i < 40; i++) {
    list = HeardBots.upsert(list, HeardBots.fromAdvert(advert(`WX-T${i}`, key(0x40 + i, 0x09), 3_000_000 + i * 1000), { now: 0 }))
  }
  assert.equal(list.length, HeardBots.max)
  assert.equal(list[0].name, 'WX-T39')
})

test("the radio's own row wins, and heard bots it lacks are added", () => {
  const radio = [{ publicKey: key(0x1d), name: 'WX-AUS', latitude: 1, longitude: 2, lastAdvertTimestamp: 9, type: 1 }]
  const heard = [
    HeardBots.fromAdvert(advert('WX-AUS', key(0x1d)), { now: 0 }),
    HeardBots.fromAdvert(advert('WX-SAT', key(0x30)), { now: 0 }),
  ]
  const merged = HeardBots.merge(radio, heard)
  assert.deepEqual(merged.map((c) => c.name), ['WX-AUS', 'WX-SAT'])
  assert.equal(merged[0].lastAdvertTimestamp, 9)
})

test('the list survives storage, and junk in storage is dropped', () => {
  const list = [HeardBots.fromAdvert(advert('WX-AUS', key(0x1d)), { now: 0 })]
  const back = HeardBots.fromStored(JSON.parse(JSON.stringify(HeardBots.toStored(list))))
  assert.deepEqual(Array.from(back[0].publicKey), Array.from(key(0x1d)))
  assert.equal(back[0].name, 'WX-AUS')
  assert.deepEqual(HeardBots.fromStored([{ publicKey: 'zz', name: 'WX-AUS' }, { name: 'x' }, null]), [])
  assert.deepEqual(HeardBots.fromStored('nonsense'), [])
})
