// Builds web/assets/basemap.json: state outlines for the offline map's base layer, derived from
// the bundle's own county outlines so the two always agree.
//
//   node tools/build-basemap.mjs
//
// A county ring segment that no neighbouring county *of the same state* shares is part of the
// state's boundary (a state line or a coast). Those segments chain into closed rings, which are
// then simplified (Douglas-Peucker, keeping original vertices, so a state line still lies exactly
// on the county lines under it). The map draws these at national zoom and only needs the 4.5 MB
// county file when it shades counties or zooms in.
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = join(here, '../../meshcore_weather/client_data/counties.geojson')
const out = join(here, '../assets/basemap.json')
const TOLERANCE = 0.004          // degrees, about 400 m
const Q = 10000

const counties = JSON.parse(readFileSync(source, 'utf8')).features
const byState = new Map()
for (const feature of counties) {
  const state = feature.properties.state
  const polygons = feature.geometry.type === 'MultiPolygon' ? feature.geometry.coordinates : [feature.geometry.coordinates]
  if (!byState.has(state)) byState.set(state, [])
  for (const polygon of polygons) for (const ring of polygon) byState.get(state).push(ring)
}

const key = ([lon, lat]) => `${Math.round(lon * Q)},${Math.round(lat * Q)}`

function boundaryRings(rings) {
  // Directed segments; one that also appears reversed is interior to the state and cancels.
  const segments = new Map()
  for (const ring of rings) {
    for (let i = 0; i + 1 < ring.length; i++) {
      const a = key(ring[i]), b = key(ring[i + 1])
      if (a === b) continue
      const reverse = `${b}|${a}`
      if (segments.has(reverse)) segments.delete(reverse)
      else segments.set(`${a}|${b}`, [a, b])
    }
  }
  const from = new Map()
  for (const [a, b] of segments.values()) {
    if (!from.has(a)) from.set(a, [])
    from.get(a).push(b)
  }
  const result = []
  for (const start of [...from.keys()]) {
    while (from.get(start)?.length) {
      const ring = [start]
      let at = start
      for (;;) {
        const next = from.get(at)?.pop()
        if (next == null) break
        ring.push(next)
        at = next
        if (at === start) break
      }
      if (ring.length >= 4 && ring[ring.length - 1] === start) result.push(ring.map((k) => k.split(',').map(Number)))
    }
  }
  return result
}

function simplify(points, tolerance) {
  if (points.length <= 4) return points
  const keep = new Uint8Array(points.length)
  keep[0] = keep[points.length - 1] = 1
  const stack = [[0, points.length - 1]]
  const t2 = (tolerance * Q) ** 2
  while (stack.length) {
    const [first, last] = stack.pop()
    const [ax, ay] = points[first], [bx, by] = points[last]
    const dx = bx - ax, dy = by - ay
    const length2 = dx * dx + dy * dy
    let worst = -1, worstDistance = 0
    for (let i = first + 1; i < last; i++) {
      const [px, py] = points[i]
      let d
      if (length2 === 0) d = (px - ax) ** 2 + (py - ay) ** 2
      else {
        const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length2))
        d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
      }
      if (d > worstDistance) { worstDistance = d; worst = i }
    }
    if (worst >= 0 && worstDistance > t2) {
      keep[worst] = 1
      stack.push([first, worst], [worst, last])
    }
  }
  return points.filter((_, i) => keep[i])
}

function area(ring) {
  let sum = 0
  for (let i = 0; i + 1 < ring.length; i++) sum += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
  return Math.abs(sum) / 2
}

const states = []
let before = 0, after = 0
for (const [code, rings] of [...byState].sort(([a], [b]) => a.localeCompare(b))) {
  const outlines = boundaryRings(rings)
  const kept = []
  for (const ring of outlines) {
    before += ring.length
    // Specks (rocks, sandbars) smaller than about 2 km² add thousands of rings and no shape.
    if (area(ring) < (0.015 * Q) ** 2 && outlines.length > 1) continue
    const simple = simplify(ring, TOLERANCE)
    if (simple.length < 4) continue
    after += simple.length
    kept.push(simple.flat())
  }
  states.push({ code, rings: kept })
}

writeFileSync(out, JSON.stringify({ version: 1, scale: Q, note: 'lon,lat pairs as integers, degrees x scale', states }))
const bytes = readFileSync(out).length
console.log(`${states.length} states, ${before} → ${after} vertices, ${(bytes / 1024).toFixed(0)} KB → ${out}`)
