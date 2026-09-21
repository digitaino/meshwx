// The map's projection, without a canvas.
//
// `MapCanvas` is a view and has no Swift counterpart, so there is no suite to port; this covers
// the one piece of it that is arithmetic rather than drawing — where a box of degrees lands in
// the world space the basemap is drawn in. It is worth a test of its own because the failure it
// guards against is silent: radar cells half a cell out sit *beside* the state lines instead of
// over them, and the picture still looks like a picture.

import test from 'node:test'
import assert from 'node:assert/strict'

import { worldRectangle } from '../src/ui/kit/MapCanvas.js'
import { MeshWXRadarTile } from '../src/meshwx/index.js'

const close = (value, wanted, message) =>
  assert.ok(Math.abs(value - wanted) < 1e-12, `${message}: ${value} vs ${wanted}`)

test('a box of degrees lands where the basemap puts it', () => {
  // Longitude is linear: 98W is 82/360 of the way round from the date line.
  const box = worldRectangle({ south: 30, west: -98, north: 31, east: -97 })
  close(box.x, (-98 + 180) / 360, 'west edge')
  close(box.width, 1 / 360, 'one degree wide')
  // y grows southwards, as the canvas's own axis does.
  assert.ok(box.height > 0, 'the northern edge is above the southern one')
})

/**
 * Mercator, not a plate carrée: a degree of latitude is taller on the map the further north it
 * is. Austin's cells and Fargo's are the same degrees and are not the same height, which is
 * exactly why this goes through the map's own projection rather than through a ratio of degrees.
 */
test('a degree of latitude is taller the further north it is', () => {
  const austin = worldRectangle({ south: 30, west: -98, north: 31, east: -97 })
  const fargo = worldRectangle({ south: 46, west: -98, north: 47, east: -97 })
  assert.ok(fargo.height > austin.height * 1.2, `${fargo.height} vs ${austin.height}`)
  // And the width is the same, because longitude is linear in Mercator.
  close(fargo.width, austin.width, 'same width')
})

/** Neighbouring cells meet exactly: a seam of basemap between two echoes is a wrong picture. */
test('the cells of one row tile the tile exactly', () => {
  const tile = MeshWXRadarTile.make({ south: 29, west: -99, zoom: 0 })
  const boxes = Array.from({ length: 32 }, (_, col) =>
    worldRectangle(MeshWXRadarTile.cellBox(tile, { row: 7, col, size: 32 })))
  for (let col = 1; col < boxes.length; col += 1) {
    close(boxes[col].x, boxes[col - 1].x + boxes[col - 1].width, `cell ${col} meets ${col - 1}`)
  }
  const span = boxes[31].x + boxes[31].width - boxes[0].x
  close(span, 2 / 360, 'the row spans the tile')
})

/**
 * The westward wrap. The map lays the western Aleutians west of Hawaii, where they are, rather
 * than a world away on the right-hand edge — so a tile whose east edge is past 180 has to keep a
 * positive width instead of folding back across the world.
 */
test('a tile on the date line keeps its width', () => {
  const box = worldRectangle({ south: 50, west: 179, north: 52, east: 181 })
  close(box.width, 2 / 360, 'two degrees wide')
  close(box.x, (179 - 360 + 180) / 360, 'laid out west of Hawaii')
  assert.ok(box.x < 0, 'west of the far edge, not on it')
})
