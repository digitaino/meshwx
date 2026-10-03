// Port of MC1Services/Tests/MeshWXTests/MeshWXRadarTests.swift, and of the bot's own
// tests/test_radar.py (the lattice and codec halves of it; the GIF reading is the bot's).
//
// Radar, type 11, spec §7D, revision 11. The lattice cases below are the reference's
// parametrised list value for value: two clients that round a tie differently ask for two
// different tiles, and the channel pays for both.

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  MeshWXDecodeError,
  MeshWXEncodeError,
  MeshWXEncoder,
  MeshWXMessageType,
  MeshWXRadar,
  MeshWXRadarBounds,
  MeshWXRadarLevel,
  MeshWXRadarTile,
  MeshWXWire,
  bytesToHex,
  decode,
  encode,
} from '../src/meshwx/index.js';

const BOT = 0x4c7a;
/** 2026-09-20 23:38 UTC, the minute printed on the fixture pictures. */
const TAKEN_MIN = 29832458;

/** A grid of `size` rows with `cells` (`[row, col, level]`) set and everything else dry. */
function rows(size, cells = []) {
  const grid = Array.from({ length: size }, () => new Array(size).fill(0));
  for (const [row, col, level] of cells) grid[row][col] = level;
  return grid;
}

function tile({ size = 32, cells = [], ...options } = {}) {
  return MeshWXEncoder.radar({
    seq: 1,
    bot: BOT,
    takenMinutes: TAKEN_MIN,
    south: 29,
    west: -99,
    zoom: 0,
    product: 1,
    rows: rows(size, cells),
    ...options,
  });
}

/**
 * A deterministic pseudo-random source.
 *
 * Deviation from the reference, which seeds Python's Mersenne Twister with 11: no two languages
 * agree on a generator's output, so the *cases* cannot be ported, only the property they check
 * — that every grid round trips, and that a 16 × 16 always fits where a 32 × 32 may not.
 */
function randoms(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

describe('MeshWXRadarTile', () => {
  /** The reference's own parametrised list (`test_the_tile_for_a_coordinate`). */
  test('theTileForACoordinate', () => {
    const cases = [
      [30.27, -97.74, 0, { south: 29, west: -99 }],      // Austin: centre 30, -98
      [32.78, -96.80, 0, { south: 32, west: -98 }],      // Dallas: centre 33, -97
      [30.5, -97.5, 0, { south: 30, west: -98 }],        // a tie rounds up, in every language
      [-0.4, 0.4, 0, { south: -1, west: -1 }],           // across the equator and Greenwich
      [30.27, -97.74, 1, { south: 28, west: -100 }],     // step 2
      [30.27, -97.74, 2, { south: 28, west: -100 }],     // step 4: centre 32, -96
      [30.27, -97.74, 3, { south: 24, west: -104 }],     // step 8: centre 32, -96
      [18.22, -66.59, 0, { south: 17, west: -68 }],      // Puerto Rico
    ];
    for (const [latitude, longitude, zoom, want] of cases) {
      assert.deepStrictEqual(
        MeshWXRadarTile.containing({ latitude, longitude, zoom }),
        { ...want, zoom },
        `${latitude},${longitude} z${zoom}`,
      );
    }
  });

  /**
   * Revision 13's detail level, zoom −1: a 1° tile on a half-degree lattice. The reference's own
   * parametrised list (`test_the_detail_tile_for_a_coordinate`), value for value.
   */
  test('theDetailTileForACoordinate', () => {
    const cases = [
      [32.78, -96.80, { south: 32.5, west: -97.5 }],   // Dallas: centre 33.0, -97.0
      [30.27, -97.74, { south: 30, west: -98 }],       // Austin: centre 30.5, -97.5
      [30.25, -97.75, { south: 30, west: -98 }],       // ties round up here too
      [-0.1, 0.1, { south: -0.5, west: -0.5 }],
    ];
    for (const [latitude, longitude, want] of cases) {
      const square = MeshWXRadarTile.containing({ latitude, longitude, zoom: MeshWXWire.radarDetailZoom });
      assert.deepStrictEqual(square, { ...want, zoom: -1 }, `${latitude},${longitude} z-1`);
      assert.equal(MeshWXRadarTile.spanDegrees(square), 1);
      assert.equal(MeshWXRadarTile.north(square), want.south + 1);
      assert.equal(MeshWXRadarTile.east(square), want.west + 1);
    }
    // A cell is 1/32°, about 3.5 km north to south, and the asked spot is on its own tile.
    const dallas = MeshWXRadarTile.containing({ latitude: 32.78, longitude: -96.8, zoom: -1 });
    assert.equal(MeshWXRadarTile.cellDegrees(dallas, { size: 32 }), 1 / 32);
    assert.ok(MeshWXRadarTile.contains(dallas, { latitude: 32.78, longitude: -96.8 }));
    assert.equal(MeshWXRadarTile.key(dallas), '32.5,-97.5,-1');
  });

  /**
   * The whole point of putting the lattice at half the span: the place asked about is never
   * nearer than a quarter of the tile to an edge, so the storm coming at it is on the picture.
   */
  test('aPlaceIsNeverNearTheEdgeOfItsTile', () => {
    const places = [[30.27, -97.74], [47.61, -122.33], [25.76, -80.19], [64.84, -147.72], [-33.9, 151.2]];
    for (let zoom = MeshWXWire.minRadarZoom; zoom <= MeshWXWire.maxRadarZoom; zoom += 1) {
      for (const [latitude, longitude] of places) {
        const square = MeshWXRadarTile.containing({ latitude, longitude, zoom });
        const span = MeshWXRadarTile.spanDegrees(square);
        assert.equal(span, 2 ** (zoom + 1));
        for (const [value, edge] of [[latitude, square.south], [longitude, square.west]]) {
          assert.ok(value - edge >= span / 4 && value - edge <= (3 * span) / 4,
            `${value} is ${value - edge} into a ${span}° tile`);
        }
      }
    }
  });

  // The lattice clamps to −1…3, as the Swift does; the encoder is where a zoom of 4 is refused.
  test('zoomOutOfRangeIsClamped', () => {
    assert.deepEqual(MeshWXRadarTile.containing({ latitude: 30.27, longitude: -97.74, zoom: 4 }),
      MeshWXRadarTile.containing({ latitude: 30.27, longitude: -97.74, zoom: 3 }));
    // −1 is a zoom now (revision 13), and −2 is the one past it.
    assert.deepEqual(MeshWXRadarTile.containing({ latitude: 30.27, longitude: -97.74, zoom: -1 }),
      { south: 30, west: -98, zoom: -1 });
    assert.deepEqual(MeshWXRadarTile.containing({ latitude: 30.27, longitude: -97.74, zoom: -2 }),
      { south: 30, west: -98, zoom: -1 });
  });

  /**
   * Row 0 is the northern row and column 0 the western one (spec §7D), which is the picture's
   * order and the opposite of latitude's.
   */
  test('cellsAreNumberedFromTheNorthWestCorner', () => {
    const square = MeshWXRadarTile.make({ south: 32, west: -98, zoom: 0 });
    assert.equal(MeshWXRadarTile.north(square), 34);
    assert.equal(MeshWXRadarTile.east(square), -96);

    const first = MeshWXRadarTile.cellBox(square, { row: 0, col: 0, size: 32 });
    assert.deepStrictEqual(first, { south: 34 - 1 / 16, west: -98, north: 34, east: -98 + 1 / 16 });
    const last = MeshWXRadarTile.cellBox(square, { row: 31, col: 31, size: 32 });
    assert.deepStrictEqual(last, { south: 32, west: -96 - 1 / 16, north: 32 + 1 / 16, east: -96 });

    // A coarse cell is twice as wide, and the corner cells still sit in the corners.
    assert.deepStrictEqual(
      MeshWXRadarTile.cellBox(square, { row: 0, col: 0, size: 16 }),
      { south: 34 - 1 / 8, west: -98, north: 34, east: -98 + 1 / 8 },
    );
  });

  test('aCoordinateFindsItsCell', () => {
    const square = MeshWXRadarTile.make({ south: 32, west: -98, zoom: 0 });
    assert.ok(MeshWXRadarTile.contains(square, { latitude: 32.78, longitude: -96.8 }));
    assert.deepStrictEqual(
      MeshWXRadarTile.cell(square, { latitude: 32.78, longitude: -96.8, size: 32 }),
      { row: 19, col: 19 },
    );
    // The centre of that cell, which is what a distance is measured to.
    const centre = MeshWXRadarTile.cellCentre(square, { row: 19, col: 19, size: 32 });
    assert.ok(Math.abs(centre.latitude - 32.78125) < 1e-9);
    assert.ok(Math.abs(centre.longitude - -96.78125) < 1e-9);

    // Off the square in each direction.
    assert.equal(MeshWXRadarTile.cell(square, { latitude: 35, longitude: -97, size: 32 }), null);
    assert.equal(MeshWXRadarTile.cell(square, { latitude: 33, longitude: -99, size: 32 }), null);
    assert.ok(!MeshWXRadarTile.contains(square, { latitude: 33, longitude: -95.9 }));

    // The edges: half-open on the north and on the east, so the southern and western edges are
    // on the tile and the other two belong to the neighbours. A place on a whole degree has to
    // read the same here, in the app and in the DM the bot sends, and on 20 September 2026 all
    // three were settled on this rule.
    assert.ok(!MeshWXRadarTile.contains(square, { latitude: 34, longitude: -98 }));
    assert.ok(MeshWXRadarTile.contains(square, { latitude: 32, longitude: -98 }));
    assert.ok(!MeshWXRadarTile.contains(square, { latitude: 33, longitude: -96 }));
    // The southern row is row 31, not a row 32 off the end of the grid: the index is clamped
    // after the division, because an index out of range here would be an index into a cell array.
    assert.deepStrictEqual(
      MeshWXRadarTile.cell(square, { latitude: 32, longitude: -98, size: 32 }), { row: 31, col: 0 },
    );
  });

  test('tilesAreComparedByTheirThreeNumbers', () => {
    const square = MeshWXRadarTile.make({ south: 32, west: -98, zoom: 0 });
    assert.equal(MeshWXRadarTile.key(square), '32,-98,0');
    assert.ok(MeshWXRadarTile.isEqual(square, { south: 32, west: -98, zoom: 0 }));
    assert.ok(!MeshWXRadarTile.isEqual(square, { south: 32, west: -98, zoom: 1 }));
    assert.ok(!MeshWXRadarTile.isEqual(square, null));
  });
});

describe('MeshWXRadar codec', () => {
  /** Spec §7D: an all-dry tile is three bits, so a clear picture is thirteen bytes. */
  test('aDryTileIsOneByteOfCells', () => {
    const data = MeshWXEncoder.radar({
      seq: 7,
      bot: BOT,
      takenMinutes: TAKEN_MIN,
      south: 29,
      west: -99,
      zoom: 0,
      product: 1,
      rows: rows(32),
      source: 1,
    });
    assert.equal(data.length, 13);
    assert.equal(data[3], (MeshWXMessageType.radar << 4) | 0x4, 'type 11, flags 0x4 = source 1');
    assert.equal(data[12], 0, 'split bit 0, level 00, then padding');

    const out = decode(data);
    assert.equal(out.name, 'radar');
    assert.equal(out.size, 32);
    assert.equal(out.coarse, false);
    assert.equal(out.partial, false);
    assert.deepStrictEqual(
      [out.south, out.west, out.zoom, out.product], [29, -99, 0, 1],
    );
    assert.equal(out.taken_min, TAKEN_MIN);
    assert.equal(out.source, 1);
    assert.equal(out.bounds, null);
    assert.deepStrictEqual(out.rows, new Array(32).fill('0'.repeat(32)));
  });

  /**
   * One heavy cell in the north-west corner of a 16 × 16 tile: split at 16, 8, 4 and 2,
   * north-west first each time. The reference's `test_the_quadtree_bit_order`, which is the one
   * test that would catch two children swapped — a round trip through this file alone would not.
   */
  test('theQuadtreeBitOrder', () => {
    const data = tile({
      size: 16, cells: [[0, 0, 3]], takenMinutes: 1, south: 0, west: 0, product: 0, source: 0,
    });
    assert.ok((data[3] & MeshWXWire.radarCoarseBit) !== 0);
    let bits = '';
    for (const byte of data.subarray(12)) bits += byte.toString(2).padStart(8, '0');
    // 16: split. 8 NW: split. 4 NW: split. 2 NW: split, then the cells 11 00 00 00.
    // Then the three dry 2-squares, 4-squares and 8-squares, each `0 00`.
    assert.ok(bits.startsWith(`1111${'11000000'}${'000'.repeat(9)}`));
    assert.ok([...bits.slice(4 + 8 + 27)].every((bit) => bit === '0'), 'the tail is padding');
    assert.equal(decode(data).rows[0], `3${'0'.repeat(15)}`);
  });

  test('everyGridRoundTrips', () => {
    const random = randoms(11);
    let done = 0;
    for (let round = 0; round < 300; round += 1) {
      const size = random() < 0.5 ? 16 : 32;
      const grid = Array.from({ length: size }, () => Array.from({ length: size }, () => {
        if (random() >= 0.15) return 0;
        const pick = random();
        return pick < 0.5 ? 1 : (pick < 0.75 ? 2 : 3);
      }));
      let data;
      try {
        data = MeshWXEncoder.radar({
          seq: 1, bot: BOT, takenMinutes: 5, south: -10, west: 170, zoom: 3, product: 14, rows: grid,
        });
      } catch (error) {
        assert.ok(error instanceof MeshWXEncodeError);
        assert.equal(size, 32, 'a coarse tile always fits');
        continue;
      }
      const out = decode(data);
      assert.deepStrictEqual(out.rows, grid.map((row) => row.join('')));
      assert.deepStrictEqual(
        [out.south, out.west, out.zoom, out.product, out.size], [-10, 170, 3, 14, size],
      );
      assert.equal(bytesToHex(encode(out)), bytesToHex(data), 're-encode is byte-identical');
      done += 1;
    }
    assert.ok(done > 100, `${done} grids fitted a packet`);
  });

  /**
   * The claim the whole design rests on: whatever the picture, the 16 × 16 form fits one packet,
   * so the bot never has to split a radar answer and there is no `>part` for one.
   */
  test('theWorstCoarseTileStillFits', () => {
    const grid = Array.from({ length: 16 }, (_, row) => Array.from(
      { length: 16 }, (__, col) => (row * 7 + col * 3) % 4,
    ));
    const data = MeshWXEncoder.radar({
      seq: 1,
      bot: BOT,
      takenMinutes: 5,
      south: 0,
      west: 0,
      zoom: 0,
      product: 0,
      rows: grid,
      bounds: [0, 15, 0, 15],
    });
    assert.ok(data.length <= MeshWXWire.maxData, `${data.length} bytes`);
  });

  test('partialTilesCarryTheirBoundsAndNothingOutsideThem', () => {
    const data = tile({
      size: 16,
      cells: [[2, 3, 1], [9, 11, 2]],
      takenMinutes: 5,
      south: 24,
      west: -100,
      zoom: 1,
      bounds: { row0: 0, row1: 9, col0: 0, col1: 15 },
      source: 0,
    });
    assert.equal(data[3] & 0x3, MeshWXWire.radarCoarseBit | MeshWXWire.radarPartialBit);
    assert.deepStrictEqual([...data.subarray(12, 16)], [0, 9, 0, 15]);

    const out = decode(data);
    assert.deepStrictEqual(out.bounds, [0, 9, 0, 15]);
    assert.deepStrictEqual(MeshWXRadarBounds.of(out), { row0: 0, row1: 9, col0: 0, col1: 15 });
    // Inside the bounds the picture answers; outside it there is no reading at all, which is
    // not the same as level 0 and must never be drawn as one.
    assert.ok(MeshWXRadar.isKnown(out, { row: 9, col: 15 }));
    assert.ok(!MeshWXRadar.isKnown(out, { row: 10, col: 0 }));
    assert.equal(MeshWXRadar.level(out, { row: 2, col: 3 }), MeshWXRadarLevel.light);
    assert.equal(MeshWXRadar.level(out, { row: 9, col: 11 }), MeshWXRadarLevel.moderate);

    // An echo where the picture does not reach is a contradiction the receiver cannot see.
    assert.throws(() => tile({
      size: 16, cells: [[12, 3, 1]], south: 24, west: -100, zoom: 1, bounds: [0, 9, 0, 15],
    }), MeshWXEncodeError);
  });

  test('boundsOutsideTheGridAreRefusedInBothDirections', () => {
    assert.throws(() => tile({ size: 16, bounds: [0, 16, 0, 15] }), MeshWXEncodeError);
    assert.throws(() => tile({ size: 16, bounds: [9, 2, 0, 15] }), MeshWXEncodeError);

    // And on the way in: a partial tile naming rows its own grid has not would have the screen
    // read a level off a cell the picture never covered.
    const data = tile({ size: 16, bounds: [0, 9, 0, 15] });
    const bad = Uint8Array.from(data);
    bad[13] = 15 + 1;
    assert.throws(() => decode(bad), (error) => error instanceof MeshWXDecodeError
      && error.kind === 'radarBoundsOutsideGrid');
  });

  /**
   * Spec §7D: "A decoder rejects a packet whose bits run out before the tree is complete; bits
   * left over after it are padding." The two halves of one rule, and the reason for it is that a
   * half-read tree leaves the rest of the grid at level 0 — which reads as clear weather.
   */
  test('aCutOffTreeIsRefusedAndPaddingIsNot', () => {
    const data = tile({ cells: [[5, 5, 2], [20, 9, 1]] });
    assert.throws(() => decode(data.subarray(0, data.length - 2)), (error) => error instanceof MeshWXDecodeError
      && error.kind === 'radarTreeTruncated');

    const padded = Uint8Array.from([...data, 0]);
    assert.deepStrictEqual(decode(padded).rows, decode(data).rows);
  });

  test('aTruncatedFixedPartIsRefusedBeforeTheCells', () => {
    const data = tile({});
    assert.throws(() => decode(data.subarray(0, 12)), MeshWXDecodeError);
    const partial = tile({ size: 16, bounds: [0, 9, 0, 15] });
    assert.throws(() => decode(partial.subarray(0, 16)), MeshWXDecodeError);
  });

  test('theEncoderRefusesWhatTheWireCannotCarry', () => {
    assert.throws(() => tile({ zoom: 4 }), MeshWXEncodeError);
    assert.throws(() => tile({ zoom: -2 }), MeshWXEncodeError);
    assert.throws(() => tile({ product: 64 }), MeshWXEncodeError);
    assert.throws(() => tile({ west: 180 }), MeshWXEncodeError);
    assert.throws(() => tile({ south: 91 }), MeshWXEncodeError);
    assert.throws(() => tile({ size: 8 }), MeshWXEncodeError);
    assert.throws(() => tile({ cells: [[0, 0, 4]] }), MeshWXEncodeError);
  });

  test('theValueTypesReadTheDecodedMessage', () => {
    const out = decode(tile({ cells: [[0, 0, 1], [31, 31, 3]], south: 32, west: -98 }));
    assert.deepStrictEqual(MeshWXRadar.tile(out), { south: 32, west: -98, zoom: 0 });
    assert.equal(MeshWXRadar.size(out), 32);
    assert.equal(MeshWXRadar.wetCells(out), 2);
    assert.equal(MeshWXRadar.level(out, { row: 0, col: 0 }), MeshWXRadarLevel.light);
    assert.equal(MeshWXRadar.level(out, { row: 31, col: 31 }), MeshWXRadarLevel.heavy);
    assert.equal(MeshWXRadar.level(out, { row: 32, col: 0 }), null);
    assert.ok(MeshWXRadar.isKnown(out, { row: 0, col: 0 }), 'a whole tile knows every cell');
    assert.equal(MeshWXRadar.cellRows(out)[31][31], 3);
    assert.ok(MeshWXRadarLevel.isWet(MeshWXRadarLevel.light));
    assert.ok(!MeshWXRadarLevel.isWet(MeshWXRadarLevel.none));
  });

  /** `MeshWXRadar.make` builds the decoded shape from the Swift's own field names. */
  test('makeBuildsTheDecodedShapeFromSwiftNames', () => {
    const grid = rows(16, [[1, 2, 3]]);
    const built = MeshWXRadar.make({
      takenMinutes: TAKEN_MIN,
      south: 24,
      west: -100,
      zoom: 1,
      product: 1,
      bounds: { row0: 0, row1: 9, col0: 0, col1: 15 },
      cells: grid.flat(),
      source: 1,
      seq: 41,
      bot: BOT,
    });
    assert.equal(built.name, 'radar');
    assert.equal(built.coarse, true, 'a 16-row grid is what the coarse flag means');
    assert.equal(built.partial, true);
    assert.deepStrictEqual(built.bounds, [0, 9, 0, 15]);
    assert.equal(built.size, 16);
    assert.equal(built.rows[1], '0030000000000000');
    // Built by hand or decoded off the air, it is the same object.
    assert.deepStrictEqual(decode(encode(built)), built);
  });
});

// Revision 13, spec §7E: the detail level travels as type 12, with its edges in quarter degrees.
describe('MeshWXRadar detail codec', () => {
  /** The reference's `test_a_detail_tile_round_trips_in_quarter_degrees`, value for value. */
  test('aDetailTileRoundTripsInQuarterDegrees', () => {
    const grid = rows(32, [[5, 7, 3], [30, 2, 1]]);
    const data = MeshWXEncoder.radar({
      seq: 3, bot: BOT, takenMinutes: TAKEN_MIN, south: -0.5, west: 179.5, zoom: -1, product: 13,
      rows: grid, source: 1,
    });
    assert.equal(data[3] >> 4, MeshWXMessageType.radarDetail);
    assert.ok(data.length <= MeshWXWire.maxData);
    assert.deepStrictEqual([...data.subarray(8, 10)], [0xfe, 0xff], 'south −2 quarter degrees');
    assert.deepStrictEqual([...data.subarray(10, 12)], [718 & 0xff, 718 >> 8], 'west 718 quarter degrees');
    assert.equal(data[12], 13 << 2, 'product 13, depth 0');
    const out = decode(data);
    assert.deepStrictEqual(
      [out.name, out.south, out.west, out.zoom, out.product], ['radar_detail', -0.5, 179.5, -1, 13],
    );
    assert.equal(out.rows[5][7], '3');
    assert.equal(out.rows[30][2], '1');
    assert.ok(MeshWXRadar.isRadarMessage(out));
    assert.deepStrictEqual(MeshWXRadar.tile(out), { south: -0.5, west: 179.5, zoom: -1 });

    // "A tile with nothing on it is 14 bytes."
    const empty = MeshWXEncoder.radar({
      seq: 3, bot: BOT, takenMinutes: TAKEN_MIN, south: 30, west: -98, zoom: -1, product: 1, rows: rows(32),
    });
    assert.equal(empty.length, 14);
  });

  /** The reference's `test_a_detail_tile_is_refused_off_its_lattice_or_with_a_depth`. */
  test('aDetailTileIsRefusedOffItsLatticeOrWithADepth', () => {
    const grid = rows(32);
    const detail = (options) => MeshWXEncoder.radar({
      seq: 1, bot: BOT, takenMinutes: 1, south: 30.5, west: -98, zoom: -1, product: 1, rows: grid, ...options,
    });
    assert.throws(() => detail({ south: 30.25 }), MeshWXEncodeError, 'a quarter, not a half');
    assert.throws(() => detail({ zoom: 0 }), MeshWXEncodeError, 'a half degree is not a zoom 0 edge');

    const good = detail({});
    const deep = Uint8Array.from(good);
    deep[12] |= 1;
    assert.throws(() => decode(deep), (error) => error instanceof MeshWXDecodeError
      && error.kind === 'radarDetailDepthReserved' && error.depth === 1);

    const off = Uint8Array.from(good);
    off[8] = 123; // 30.75: a quarter, not a half
    off[9] = 0;
    assert.throws(() => decode(off), (error) => error instanceof MeshWXDecodeError
      && error.kind === 'radarDetailOffLattice' && error.south === 30.75 && error.west === -98);
  });

  test('aDetailTileCarriesTheSameFlagsAndBoundsAsType11', () => {
    const data = MeshWXEncoder.radar({
      seq: 2, bot: BOT, takenMinutes: TAKEN_MIN, south: 24, west: -98.5, zoom: -1, product: 1,
      rows: rows(16, [[2, 3, 3]]), bounds: [0, 7, 0, 15], source: 1,
    });
    const out = decode(data);
    assert.equal(out.type, MeshWXMessageType.radarDetail);
    assert.equal(out.flags, 0x7, 'coarse, partial, off the dish');
    assert.deepStrictEqual(out.bounds, [0, 7, 0, 15]);
    assert.ok(MeshWXRadar.isUnknown(out, { row: 8, col: 0 }));
    // The fixed part is a byte longer than type 11's: a cut there is refused before the cells.
    assert.throws(() => decode(data.subarray(0, MeshWXWire.radarDetailFixedSize)), MeshWXDecodeError);
    assert.throws(() => decode(data.subarray(0, 17)), MeshWXDecodeError);
  });

  /** `make` at zoom −1 is a type 12 object, and the encoder writes it as one. */
  test('makeAtTheDetailLevelIsType12', () => {
    const built = MeshWXRadar.make({
      takenMinutes: TAKEN_MIN, south: 32.5, west: -97.5, zoom: -1, product: 1, rows: rows(32, [[0, 0, 2]]),
      source: 1, seq: 7, bot: BOT,
    });
    assert.equal(built.type, MeshWXMessageType.radarDetail);
    assert.equal(built.name, 'radar_detail');
    assert.deepStrictEqual(decode(encode(built)), built);
    // And an object that says type 11 with zoom −1 is still written as type 12: the zoom decides.
    assert.equal(encode({ ...built, type: MeshWXMessageType.radar, name: 'radar' })[3] >> 4, 12);
  });
});
