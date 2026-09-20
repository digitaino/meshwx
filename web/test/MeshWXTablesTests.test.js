// Port of MC1Services/Tests/MeshWXTests/MeshWXTablesTests.swift
//
// The preload bundle (spec §9) and the search rules (spec §11).
//
// The expected values are read off the kit's own `client_data` files, not off this
// implementation: if a future bundle renumbers something, these fail loudly instead of agreeing
// with whatever the code now does.

import { test, describe, before } from 'node:test';
import assert from 'node:assert/strict';

import { MeshWXTables, MeshWXGeo, MeshWXSeverity } from '../src/meshwx/index.js';
import { nodeBundleLoader } from '../src/meshwx/nodeLoader.js';
import { sharedTables, objectLoader } from './helpers/meshwx-vectors.js';

describe('MeshWX tables', () => {
  let tables;
  before(async () => { tables = await sharedTables(); });

  test('bundleLoads', () => {
    assert.equal(tables.protocolVersion, 14, 'protocol.json version is 14 for v5.0 revision 10');
    assert.equal(tables.offices.length, 127, '125 WFOs, then NHC and WNS (spec rev 3 §9)');
    assert.equal(tables.stations.length, 2237);
    assert.equal(tables.states.length, 78);
    assert.equal(tables.points.length, 1958); // 1873 until revision 10 appended the nine offices the first build missed
    assert.equal(tables.places.length, 34937);
  });

  test('nationalCentresAreAppendedAfterTheWFOs', () => {
    // Appended, never sorted in: every WFO keeps its index.
    assert.equal(tables.officeCode(0), 'ABQ');
    assert.equal(tables.officeCode(124), 'VEF');
    assert.equal(tables.officeCode(125), 'NHC');
    assert.equal(tables.officeCode(126), 'WNS');
    assert.ok(tables.isNationalCentre(126));
    assert.ok(tables.isNationalCentre(125));
    assert.ok(!tables.isNationalCentre(35));
  });

  test('wireIndicesResolveToTheDocumentedCodes', () => {
    // These three are the indices in the kit's own vectors: a warning from EWX over Texas, and
    // KAUS in the observation batch.
    assert.equal(tables.officeCode(35), 'EWX');
    assert.equal(tables.stateCode(42), 'TX');
    assert.equal(tables.stationICAO(202), 'KAUS');
    assert.equal(tables.stationICAO(860), 'KGTU');
    assert.equal(tables.stationICAO(976), 'KHYI');
    assert.equal(tables.stationIndex({ forICAO: 'KAUS' }), 202);
    assert.equal(tables.stationIndex({ forICAO: 'kaus' }), 202);
  });

  test('unknownIndicesReturnNilAndStillLabel', () => {
    assert.equal(tables.officeCode(250), null);
    assert.equal(tables.stationICAO(60000), null);
    assert.equal(tables.stateCode(200), null);
    assert.equal(tables.officeLabel(250), 'unknown (#250)');
    assert.equal(tables.stationLabel(60000), 'unknown (#60000)');
    assert.equal(tables.eventLabel({ for: 250 }), 'unknown (#250)');
  });

  test('eventBytesResolveToVTECAndNames', () => {
    assert.equal(tables.vtec({ for: 3 }), 'SV.W');
    assert.equal(tables.eventByCode.get('SV.W'), 3);
    const name = tables.eventName({ for: 3 });
    assert.equal(name.long, 'Severe Thunderstorm Warning');
    assert.equal(name.short, 'SVR TSTM WRN');
    assert.equal(tables.vtec({ for: 24 }), 'WS.W');
    assert.equal(tables.eventName({ for: 24 }).long, 'Winter Storm Warning');
  });

  test('severityComesFromTheSignificanceLetter', () => {
    assert.equal(tables.severity({ for: 3 }), MeshWXSeverity.warning); // SV.W
    assert.equal(tables.severity({ for: 4 }), MeshWXSeverity.watch); // SV.A
    assert.equal(tables.severity({ for: 14 }), MeshWXSeverity.advisory); // HT.Y
    // SPS has no significance letter at all and is a statement (spec §3).
    const sps = tables.eventByCode.get('SPS');
    assert.ok(sps != null);
    assert.equal(tables.severity({ for: sps }), MeshWXSeverity.statement);
  });

  test('skyCodeNamesComeFromTheBundle', () => {
    assert.equal(tables.skyNames.get(3), 'broken');
    assert.equal(tables.skyNames.get(10), 'thunderstorm');
    assert.equal(tables.skyNames.size, 16);
  });

  test('forecastPointsAreIndexedByWirePosition', () => {
    const point = tables.point({ at: 102 });
    assert.ok(point != null);
    assert.ok(point.name.includes('Austin'), `point 102 is ${point.name}`);
    assert.equal(point.index, 102);
    assert.equal(point.office, 'EWX');
    assert.equal(point.zone, 'TXZ192');
  });

  test('nearestPointToAustinIsInTravis', () => {
    // Downtown Austin. The nearest bundled point is Camp Mabry (index 103), in Travis county,
    // zone TXZ192 — which is what `>f <index>` should be sent for.
    const point = tables.nearestPoint({ toLat: 30.27, lon: -97.74 });
    assert.ok(point != null);
    assert.ok(point.name.includes('Travis'), `nearest point is ${point.name}`);
    assert.equal(point.zone, 'TXZ192');
    const distance = MeshWXGeo.distanceMiles({
      fromLat: 30.27, fromLon: -97.74, toLat: point.lat, toLon: point.lon,
    });
    assert.ok(distance < 10);
  });

  test('stationDetailsAndSearch', () => {
    const kaus = tables.station({ icao: 'KAUS' });
    assert.ok(kaus != null);
    assert.equal(kaus.name, 'AUSTIN-BERGSTROM INTL AIRPORT');
    assert.equal(kaus.state, 'TX');
    assert.ok(Math.abs(kaus.lat - 30.183) < 0.0005);
    // The wire index and the details table agree.
    assert.deepStrictEqual(tables.station({ at: 202 }), kaus);

    assert.ok(tables.searchStations({ query: 'KAU' }).some((s) => s.icao === 'KAUS'));
    assert.ok(tables.searchStations({ query: 'bergstrom' }).some((s) => s.icao === 'KAUS'));
    assert.equal(tables.searchStations({ query: '' }).length, 0);
  });

  test('zonesCountiesAndOfficesResolve', () => {
    const zone = tables.zone('TXZ192');
    assert.ok(zone != null);
    assert.equal(zone.name, 'Travis');
    assert.equal(zone.office, 'EWX');

    const county = tables.county('TXC453');
    assert.ok(county != null);
    assert.equal(county.name, 'Travis');
    assert.equal(county.state, 'TX');

    const office = tables.office('EWX');
    assert.ok(office != null);
    assert.deepStrictEqual(office.states, ['TX']);
    assert.equal(tables.zone('XXZ999'), null);
    assert.equal(tables.county('XXC999'), null);
  });

  test('placeSearchRanksByDistanceThenPopulation', () => {
    // Spec §11's own example: Round Rock exists in TX and AZ. Asked from Austin, the one 20
    // miles up the road has to come first, even though ranking by population alone would also
    // get it right — so the AZ one must be present to prove the search is not just filtering by
    // state.
    const fromAustin = tables.searchPlaces({ query: 'round rock', nearLat: 30.27, lon: -97.74 });
    assert.deepStrictEqual(fromAustin.slice(0, 2).map((p) => p.state), ['TX', 'AZ']);
    assert.equal(fromAustin[0].name, 'ROUND ROCK');

    // Same query with no anchor: biggest first, which is still TX.
    const unanchored = tables.searchPlaces({ query: 'round rock' });
    assert.deepStrictEqual(unanchored.slice(0, 2).map((p) => p.state), ['TX', 'AZ']);
    assert.ok(unanchored[0].population > 100_000);

    // Prefix, not substring: "rock" must not pull in "ROUND ROCK".
    assert.ok(!tables.searchPlaces({ query: 'ock' }).some((p) => p.name === 'ROUND ROCK'));
    assert.equal(tables.searchPlaces({ query: 'ROUND ROCK' }).length, 2);
    assert.equal(tables.searchPlaces({ query: 'round rock', limit: 1 }).length, 1);
    assert.equal(tables.searchPlaces({ query: '   ' }).length, 0);
  });

  test('warningAreasResolveToNamedCounties', () => {
    // The area list from the kit's severe thunderstorm vector: Hays and Travis.
    const runs = [
      { state: 42, county: true, start: 209, run: 1 },
      { state: 42, county: true, start: 453, run: 1 },
    ];
    const areas = tables.namedAreas({ for: runs });
    assert.deepStrictEqual(areas.map((a) => a.ugc), ['TXC209', 'TXC453']);
    assert.deepStrictEqual(areas.map((a) => a.name), ['Hays', 'Travis']);
    assert.ok(areas.every((a) => a.state === 'TX' && a.isCounty));
    assert.ok(areas.every((a) => a.lat != null && a.lon != null));
  });

  test('zoneRunExpandsToEveryZoneItCovers', () => {
    // The winter storm vector: zones 191-194 as one run.
    const areas = tables.namedAreas({ for: [{ state: 42, county: false, start: 191, run: 4 }] });
    assert.deepStrictEqual(areas.map((a) => a.ugc), ['TXZ191', 'TXZ192', 'TXZ193', 'TXZ194']);
    assert.deepStrictEqual(areas.map((a) => a.name), ['Hays', 'Travis', 'Bastrop', 'Lee']);
  });

  test('areasSurviveAnUnknownUGC', () => {
    // A county number this bundle has no row for still yields an area, unnamed.
    const areas = tables.namedAreas({ for: [{ state: 42, county: true, start: 997, run: 1 }] });
    assert.equal(areas.length, 1);
    assert.equal(areas[0].ugc, 'TXC997');
    assert.equal(areas[0].name, null);
    assert.equal(areas[0].lat, null);
  });

  test('missingResourcesLoadEmptyRatherThanCrashing', () => {
    // A resource that did not copy must not take the app down on launch.
    const empty = new MeshWXTables({});
    assert.equal(empty.protocolVersion, 0);
    assert.equal(empty.offices.length, 0);
    assert.equal(empty.officeCode(35), null);
    assert.equal(empty.officeLabel(35), 'unknown (#35)');
    assert.equal(empty.nearestPoint({ toLat: 30.27, lon: -97.74 }), null);
    assert.equal(empty.searchPlaces({ query: 'round rock' }).length, 0);
    assert.equal(
      empty.namedAreas({ for: [{ state: 42, county: true, start: 453, run: 1 }] }).length, 0,
    );
  });

  /**
   * The bundle appends the National Hurricane Center and the Storm Prediction Center to the
   * office list; neither is a forecast office.
   */
  test('nationalCentresAreKnownByCode', async () => {
    const loader = objectLoader({
      index: { offices: ['ABQ', 'EWX', 'NHC', 'WNS', 'XYZ'], stations: [], states: [] },
      // The bot's rows: a centre adds a name and lists no states; `zone_count` is not read.
      wfos: {
        EWX: { states: ['TX'], lat: 29.6789, lon: -98.614, zone_count: 33 },
        NHC: {
          states: [], lat: 25.7543, lon: -80.3838, zone_count: 0, name: 'National Hurricane Center',
        },
        XYZ: { states: [], lat: 1, lon: 2, zone_count: 0, name: 'Some Future Centre' },
      },
    });
    const small = await MeshWXTables.from(loader);
    assert.ok(!small.isNationalCentre(1));
    assert.ok(small.isNationalCentre(2));
    assert.ok(small.isNationalCentre(3), 'by code, with no wfos row');
    assert.ok(small.isNationalCentre(4), 'by its row listing no states');
    assert.ok(!small.isNationalCentre(200), 'an index the bundle does not know');
    assert.deepStrictEqual(small.office('EWX').states, ['TX']);
    assert.equal(small.office('EWX').name, null);
    assert.equal(small.office('NHC').name, 'National Hurricane Center');
  });

  /**
   * Not an assertion about speed — a number for the report, and a guard that the bundle is being
   * read at all.
   */
  test('reportsBundleLoadTime', async () => {
    const started = performance.now();
    const fresh = await MeshWXTables.from(nodeBundleLoader());
    console.log(`MeshWX: nine JSON tables loaded in ${Math.round(performance.now() - started)} ms`);
    assert.equal(fresh.places.length, 34937);
  });
});

/** Naming a coordinate for a screen header. */
describe('MeshWX nearest place', () => {
  let tables;
  before(async () => { tables = await sharedTables(); });

  test('downtownAustinIsNamedAustin', () => {
    const place = tables.nearestPlace({ toLat: 30.2672, lon: -97.7431 });
    assert.ok(place != null);
    assert.equal(place.name.toUpperCase(), 'AUSTIN');
    assert.equal(place.state, 'TX');
  });

  test('roundRockIsNamedRoundRock', () => {
    const place = tables.nearestPlace({ toLat: 30.5083, lon: -97.6789 });
    assert.ok(place != null);
    assert.equal(place.name.toUpperCase(), 'ROUND ROCK');
  });

  test('nothingIsNamedFromBeyondTheRadius', () => {
    // 250 km out in the Gulf of Mexico.
    assert.equal(tables.nearestPlace({ toLat: 26.5, lon: -93.5 }), null);
  });

  test('sanJuanAirportIsItsOwnNearestStation', () => {
    const station = tables.nearestStation({ toLat: 18.433, lon: -66.011 });
    assert.ok(station != null);
    assert.equal(station.icao, 'TJSJ');
  });

  test('noStationIsOfferedFromBeyondTheRadius', () => {
    // The middle of the North Atlantic.
    assert.equal(tables.nearestStation({ toLat: 30.0, lon: -50.0 }), null);
  });
});
