// The radar screen's own decisions (revision 13 §3), without a DOM.
//
// `WeatherRadarView` is a view and has no Swift test to port; the rules it adds on top of the
// screen layer — which segments the width control has, what the Detail segment draws and asks
// for, the loop row, which frame is on screen and how long it stays — live in
// `radarScreenModel` so they can be pinned here. The canvas is not exercised: a stand-in `Path2D`
// is enough to see that a frame's map layer is built once.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  RADAR_DETAIL, RADAR_LOOP_TIMING, radarDrawing, radarLoopDelay, radarPrint, radarScreenModel, radarWidths,
} from '../src/ui/radio/WeatherRadarView.js'
import { WeatherRequest } from '../src/weather/index.js'
import { storedRadarTile, WeatherPhoneFixture as P } from './helpers/screen-fixture.js'

// The map layer is a canvas path; a node test has none, and only needs to see it built once.
globalThis.Path2D ??= class { rect() {} }

const austin = P.place(P.austin)
/** A spot picked on the map near Dallas: its square is 32.5 N to 33.5 N, 97.5 W to 96.5 W. */
const spot = { latitude: 32.78, longitude: -96.8 }
const detailSquare = { south: 32.5, west: -97.5, zoom: -1 }
const dallasLocal = { south: 32, west: -98, zoom: 0 }
const austinLocal = { south: 29, west: -99, zoom: 0 }
const at = (tile, ago, options = {}) => storedRadarTile({ ...tile, takenMinutes: P.nowMinutes - ago, ...options })
const model = (options) => radarScreenModel({ place: austin, tiles: [], zoom: 0, now: P.now, ...options })

test('the Detail segment appears once a spot is picked, last of the four', () => {
  assert.deepStrictEqual(radarWidths({ spot: null }), [0, 1, 2])
  assert.deepStrictEqual(radarWidths({ spot }), [0, 1, 2, RADAR_DETAIL])
  assert.equal(RADAR_DETAIL, -1)

  const before = model({})
  assert.deepStrictEqual(before.widths, [0, 1, 2])
  assert.equal(before.showsHint, true, '"Tap the map for a detailed picture of that spot."')
  assert.equal(before.outline, null)

  const after = model({ spot, zoom: RADAR_DETAIL })
  assert.deepStrictEqual(after.widths, [0, 1, 2, -1])
  assert.equal(after.showsHint, false)
  assert.equal(after.isDetail, true)
  // Without a spot the Detail zoom has nothing to be about, and reads as Local.
  const stray = model({ zoom: RADAR_DETAIL })
  assert.equal(stray.isDetail, false)
  assert.equal(stray.zoom, 0)
})

/**
 * "The camera frames the 1° square, which is outlined." With nothing held: "Not asked for yet",
 * the ask "Ask for detail here" at the spot, and the loop's own ask for the same square.
 */
test('the Detail segment frames and outlines the square, and asks at the spot', () => {
  const view = model({ spot, zoom: RADAR_DETAIL })
  assert.equal(view.card.kind, 'missing')
  assert.equal(view.picture, null)
  assert.deepStrictEqual(view.tile, detailSquare)
  assert.deepStrictEqual(view.outline, detailSquare)
  assert.equal(WeatherRequest.wireText(view.request), '>radar 32.780,-96.800 z-1')
  assert.equal(view.askTitle, 'detailAsk')
  assert.equal(WeatherRequest.wireText(view.loopRequest), '>radar 32.780,-96.800 z-1 loop')
  assert.equal(view.canPlay, false)
  assert.equal(view.canAskForMore, true)

  const drawing = radarDrawing({ app: null, snapshot: { place: austin, alerts: [] }, model: view })
  const outline = drawing.shapes.find((shape) => shape.id === 'detail-square')
  assert.ok(outline != null, 'the square is outlined')
  assert.equal(outline.fill, false)
  assert.deepStrictEqual(drawing.bounds, { minLatitude: 32.5, maxLatitude: 33.5, minLongitude: -97.5, maxLongitude: -96.5 })
  // No outline on the other widths.
  const local = radarDrawing({ app: null, snapshot: { place: austin, alerts: [] }, model: model({ spot, zoom: 0 }) })
  assert.equal(local.shapes.some((shape) => shape.id === 'detail-square'), false)
})

/**
 * "With no detail picture but a Local one for the spot, draws Local with the line 'No detailed
 * picture of this spot. Showing Local.'" The square asked about is still the one framed, the ask
 * still asks for detail, and the loop is Local's, which is what is drawn.
 */
test('Local stands in on the Detail segment, and its frames are the loop', () => {
  const tiles = [at(dallasLocal, 5), at(dallasLocal, 20)]
  const view = model({ spot, zoom: RADAR_DETAIL, tiles })
  assert.equal(view.card.kind, 'local')
  assert.equal(view.isFallback, true)
  assert.deepStrictEqual(view.picture.stored.tile, dallasLocal)
  assert.deepStrictEqual(view.tile, detailSquare, 'the camera frames the square asked about')
  assert.equal(view.askTitle, 'detailAsk', 'nothing at the detail level is held')
  assert.equal(view.loop.frames.length, 2)
  assert.equal(view.canPlay, true)
  assert.equal(view.shown, view.picture.stored)

  // With the detail picture held it is drawn, and the ask reads "Ask for a newer picture".
  const held = model({ spot, zoom: RADAR_DETAIL, tiles: [...tiles, at(detailSquare, 10)] })
  assert.equal(held.card.kind, 'held')
  assert.equal(held.isFallback, false)
  assert.equal(held.askTitle, 'askNewer')
  assert.equal(held.loop.frames.length, 1, "the detail square's own loop")
  assert.equal(held.canPlay, false)
})

/**
 * The loop row: Play / Pause from two frames, "Ask for the last hour" while fewer than five, the
 * ask listing the frames held newest first, as many as fit.
 */
test('the loop row offers what the frames allow', () => {
  const three = [0, 15, 30].map((ago) => at(austinLocal, ago))
  const view = model({ tiles: three })
  assert.equal(view.canPlay, true)
  assert.equal(view.canAskForMore, true)
  assert.equal(
    WeatherRequest.wireText(view.loopRequest),
    `>radar 30.267,-97.743 loop ${WeatherRequest.utcHourMinute(P.nowMinutes)} ${WeatherRequest.utcHourMinute(P.nowMinutes - 15)}`,
  )
  assert.equal(view.loop.hasGap, false)

  const full = model({ tiles: [0, 15, 30, 45, 60].map((ago) => at(austinLocal, ago)) })
  assert.equal(full.canAskForMore, false, 'a full hour plays without asking')

  const gappy = model({ tiles: [0, 45].map((ago) => at(austinLocal, ago)) })
  assert.equal(gappy.loop.hasGap, true, '"Some pictures from this hour are missing."')

  const none = model({})
  assert.equal(none.canPlay, false)
  assert.equal(none.canAskForMore, true)
  assert.equal(WeatherRequest.wireText(none.loopRequest), '>radar 30.267,-97.743 loop')

  // A width the place has no coordinate for asks for nothing at all.
  const nowhere = radarScreenModel({ place: null, tiles: [], zoom: 0, now: P.now })
  assert.equal(nowhere.loopRequest, null)
  assert.equal(nowhere.canAskForMore, false)
})

/**
 * "While playing or paused on an older frame, the time line shows that frame's own time and place
 * in the loop: '6:08 PM · 2 of 5', in the caution tone like any old picture" — from thirty
 * minutes, by the frame's own age. "The summary sentences always describe the newest picture."
 */
test('the frame on screen, and when the time line names it', () => {
  const tiles = [0, 15, 30].map((ago) => at(austinLocal, ago))
  const resting = model({ tiles })
  assert.equal(resting.frame, null)
  assert.equal(resting.shown.radar.taken_min, P.nowMinutes)

  const older = model({ tiles, frameIndex: 0 })
  assert.equal(older.frame.number, 1)
  assert.equal(older.frame.count, 3)
  assert.equal(older.frame.isOld, true, 'thirty minutes old')
  assert.equal(older.shown.radar.taken_min, P.nowMinutes - 30)
  assert.equal(older.picture.stored.radar.taken_min, P.nowMinutes, 'the summary stays on the newest')
  assert.equal(older.summary, older.picture.summary)
  assert.equal(model({ tiles, frameIndex: 1 }).frame.isOld, false, 'fifteen minutes is not old yet')

  // Paused on the newest is the picture at rest; playing on it names it, by its own age.
  assert.equal(model({ tiles, frameIndex: 2 }).frame, null)
  const playing = model({ tiles, frameIndex: 2, playing: true })
  assert.equal(playing.frame.number, 3)
  assert.equal(playing.frame.isOld, false, 'the newest picture is minutes old')
  const stale = model({ tiles: [40, 55].map((ago) => at(austinLocal, ago)), frameIndex: 1, playing: true })
  assert.equal(stale.frame.isOld, true, 'and in the caution tone once it is old')

  // An index the loop no longer has is the picture at rest.
  assert.equal(model({ tiles, frameIndex: 7, playing: true }).frame, null)
})

/** "Each frame shows for 0.8 s and the newest for 2 s." */
test('each frame shows for 0.8 seconds and the newest for 2', () => {
  assert.equal(RADAR_LOOP_TIMING.frameMilliseconds, 800)
  assert.equal(RADAR_LOOP_TIMING.newestMilliseconds, 2000)
  assert.deepStrictEqual(
    [0, 1, 2, 3, 4].map((index) => radarLoopDelay({ index, count: 5 })), [800, 800, 800, 800, 2000],
  )
})

/**
 * "Each frame's drawing is made once, not on every step", and a step never moves the camera: the
 * frame is outside the fingerprint that re-frames the map.
 */
test('a step of the loop swaps a layer built once, and does not re-frame the map', () => {
  const tiles = [0, 15].map((ago) => at(austinLocal, ago, { cells: [[3, 4, 2]] }))
  const snapshot = { place: austin, alerts: [] }
  const first = model({ tiles, frameIndex: 0, playing: true })
  const second = model({ tiles, frameIndex: 1, playing: true })
  const again = model({ tiles, frameIndex: 0, playing: true })

  const layer = radarDrawing({ app: null, snapshot, model: first }).cellLayer
  assert.ok(layer != null)
  assert.notEqual(radarDrawing({ app: null, snapshot, model: second }).cellLayer, layer, 'another frame')
  assert.equal(radarDrawing({ app: null, snapshot, model: again }).cellLayer, layer, 'built once, kept')

  assert.equal(radarPrint({ app: null, snapshot, model: first }), radarPrint({ app: null, snapshot, model: second }))
  // A new square does re-frame: that is what picking a spot is for.
  assert.notEqual(
    radarPrint({ app: null, snapshot, model: first }),
    radarPrint({ app: null, snapshot, model: model({ spot, zoom: RADAR_DETAIL, tiles }) }),
  )
})
