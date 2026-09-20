// Port of MC1/Views/Tools/Weather/WeatherScreenBuilder.swift (docs/PORTING.md).

import { t } from '../l10n.js'
import {
  MeshWXGeo,
  MeshWXGeometry,
  MeshWXTables,
  MeshWXWarningIdentity,
} from '../meshwx/index.js'
import {
  WeatherBot,
  WeatherChannel,
  WeatherSessionInfo,
  WeatherStoredDigest,
  WeatherStoredWarning,
  WeatherTransportLink,
} from '../weather/index.js'
import {
  WeatherAreaGeometry,
  WeatherConditions,
  WeatherLocationSample,
  WeatherNames,
  WeatherNearbyStation,
  WeatherPage,
  WeatherPlace,
  WeatherPrimaryStation,
  WeatherRadioRow,
  WeatherScreenSnapshot,
  WeatherWarningBanner,
} from '../screen/index.js'

/** Where a page's place comes from: nothing, a searched place, or the phone's own fix. */
export const WeatherPlaceInput = Object.freeze({
  none: Object.freeze({ kind: 'none' }),
  searched(value) {
    return { kind: 'searched', value }
  },
  location(value) {
    return { kind: 'location', value }
  },

  /** The Swift's `Hashable` conformance: what tells "the place has not moved" from "it has". */
  isEqual(lhs, rhs) {
    if (lhs == null || rhs == null) return lhs === rhs
    if (lhs.kind !== rhs.kind) return false
    if (lhs.kind === 'none') return true
    return JSON.stringify(lhs.value) === JSON.stringify(rhs.value)
  },
})

/** Facts that depend only on the place, computed once per place value. */
export const WeatherPlaceFacts = Object.freeze({
  make({
    input,
    label = null,
    hasStateCode = false,
    stateCode = null,
    hasCounty = false,
    county = null,
    zoneUGC = null,
    hasNearbyStation = false,
    nearbyStation = null,
  }) {
    return {
      input,
      /** The label for a location place. */
      label,
      hasStateCode,
      stateCode,
      /** Looked up once the outlines were loaded. */
      hasCounty,
      county,
      /**
       * The place's forecast zone, from the same lookup: what a place outside the bot's area is
       * asked about by name (docs/MESHWX_UI.md §11).
       */
      zoneUGC,
      /** The nearest bundled weather station within 80 km, looked up once per place. */
      hasNearbyStation,
      nearbyStation,
    }
  },
})

/**
 * What the model hands to a build: the service, plain values, and the caches from the last build,
 * so the thirty-second rebuild reads no 35,000-place table twice.
 *
 * Deviations from the Swift (docs/PORTING.md):
 *
 * - No `contactService`, `dataStore` or `radioID`. On the web the app already holds the contacts
 *   and the channels in memory (`WeatherHost`), so they are passed in rather than fetched, and
 *   the `contacts`/`channels` caches the Swift keeps to avoid database reads are gone with them.
 * - No `offlineStates`. The web's `WeatherService` exists from boot whether or not a radio is
 *   connected, so there is no second path that reads the state file directly.
 * - `calendar` is `timeZone`, an IANA id.
 * - `tables` and `geometry` are passed rather than read off the shared singletons, so a test can
 *   hand over its own.
 */
export const WeatherBuildRequest = Object.freeze({
  PlaceInput: WeatherPlaceInput,

  make({
    weatherService = null,
    preferredBotID = null,
    pageID = WeatherPage.myLocationID,
    place = WeatherPlaceInput.none,
    isRadioConnected,
    transportLink = null,
    isChannelSyncDone,
    firmwareSupportsWeather = null,
    firmwareVersion = '',
    now,
    timeZone,
    locale,
    contacts = [],
    channels = [],
    placeFacts = null,
    stationTowns = {},
    tables = MeshWXTables.shared,
    geometry = MeshWXGeometry.shared,
  }) {
    return {
      weatherService,
      preferredBotID,
      /**
       * The page this build answers for, stamped into the snapshot and the context it produces
       * (docs/MESHWX_UI.md §13). A build is asked for one page and belongs to that page for good:
       * the pager can be swiped while it runs, and the result must not land under another name.
       */
      pageID,
      place,
      isRadioConnected,
      transportLink,
      isChannelSyncDone,
      firmwareSupportsWeather,
      firmwareVersion,
      now,
      timeZone,
      locale,
      contacts,
      channels,
      placeFacts,
      stationTowns,
      tables,
      geometry,
    }
  },
})

/** One weather radio as the About sheet lists it. */
export const WeatherBotRow = Object.freeze({
  make({ botID, bot = null, lastHeardAt = null, lastLiveHeardAt = null, feed = null }) {
    return { botID, bot, lastHeardAt, lastLiveHeardAt, feed }
  },

  id(row) {
    return row.botID
  },
})

/** A county or zone by code and display name. */
export const WeatherAreaName = Object.freeze({
  make({ ugc, name }) {
    return { ugc, name }
  },
})

/**
 * Facts the screen needs beyond the snapshot, computed in the same pass because each needs the
 * tables, the geometry or the raw per-bot state.
 *
 * `warningIssuedAt` and `warningSource` are keyed by `MeshWXWarningIdentity.key` — "3.35.42" —
 * because a JS dictionary key is a string (docs/PORTING.md §3).
 */
export const WeatherScreenContext = Object.freeze({
  make({
    page = null,
    bots = [],
    botRows = [],
    channelSlot = null,
    session = WeatherSessionInfo.make(),
    placeStateCode = null,
    placeOffice = null,
    placeCounty = null,
    placeZoneUGC = null,
    sourceState = null,
    needsGeometry = false,
    isGeometryLoaded = false,
    nearestStationTown = null,
    nearbyStation = null,
    conditions = WeatherConditions.noPlace,
    banner = null,
    radioRow = null,
    warningIssuedAt = {},
    warningSource = {},
  } = {}) {
    return {
      page,
      bots,
      botRows,
      channelSlot,
      session,
      placeStateCode,
      placeOffice,
      placeCounty,
      placeZoneUGC,
      sourceState,
      needsGeometry,
      isGeometryLoaded,
      nearestStationTown,
      nearbyStation,
      conditions,
      banner,
      radioRow,
      warningIssuedAt,
      warningSource,
    }
  },
})

/**
 * One page's build: the snapshot and the facts that came with it, which are only ever read
 * together and only ever for that page (docs/MESHWX_UI.md §13).
 */
export const WeatherPageBuild = Object.freeze({
  make({ snapshot, context }) {
    return { snapshot, context }
  },

  pageID(build) {
    return build.snapshot.page.pageID
  },
})

export const WeatherBuildResult = Object.freeze({
  make({ page, placeFacts, stationTowns }) {
    return { page, placeFacts, stationTowns }
  },
})

export const WeatherScreenBuilder = Object.freeze({
  labelReachKilometres: 25,

  async build(request) {
    const tables = request.tables
    const geometry = request.geometry
    const now = request.now

    const states = request.weatherService == null ? {} : await request.weatherService.allStates()
    const session = request.weatherService == null ? WeatherSessionInfo.make() : request.weatherService.sessionInfo()

    const contacts = request.contacts ?? []
    const channels = request.channels ?? []

    let facts =
      request.placeFacts != null && WeatherPlaceInput.isEqual(request.placeFacts.input, request.place)
        ? { ...request.placeFacts }
        : WeatherPlaceFacts.make({ input: request.place })
    const place = WeatherScreenBuilder.resolvePlace(request.place, { facts, states, tables, now })
    let bots = WeatherBot.bots({
      from: contacts,
      near: place == null ? null : { latitude: place.coordinate.latitude, longitude: place.coordinate.longitude },
    })
    // The bridge's own bot, listed as announced beside the advertised ones so the About sheet
    // and every "from" line name it. Nothing but a bot bridge reports a link. The snapshot makes
    // the same addition, so either path is complete.
    if (request.transportLink != null) bots = WeatherTransportLink.announcing(request.transportLink, bots)
    const channelSlot = await WeatherChannel.existingSlot({ in: channels })

    const inputs = WeatherScreenSnapshot.Inputs.make({
      states,
      bots,
      preferredBotID: request.preferredBotID,
      place,
      pageID: request.pageID,
      isRadioConnected: request.isRadioConnected,
      transportLink: request.transportLink,
      firmwareSupportsWeather: request.firmwareSupportsWeather,
      firmwareVersion: request.firmwareVersion,
      // Before the channel sync the table is empty: no claim that #meshwx is missing.
      hasWeatherChannel: channelSlot != null || !request.isChannelSyncDone,
      session,
      now,
      timeZone: request.timeZone,
    })
    const snapshot = WeatherScreenSnapshot.make(inputs, {
      geometry: WeatherAreaGeometry.of(geometry),
      tables,
    })

    const isGeometryLoaded = geometry.isZoneFileLoaded && geometry.isCountyFileLoaded
    const context = WeatherScreenContext.make({
      page: snapshot.page,
      bots,
      channelSlot,
      session,
      isGeometryLoaded,
      botRows: snapshot.knownBotIDs.map((botID) => {
        const state = states[String(botID)] ?? null
        const digest = state?.digest ?? null
        return WeatherBotRow.make({
          botID,
          bot: bots.find((one) => WeatherBot.botID(one) === botID) ?? null,
          lastHeardAt: state?.lastHeardAt ?? null,
          lastLiveHeardAt: state?.lastLiveHeardAt ?? null,
          feed: digest == null ? null : WeatherStoredDigest.feed(digest),
        })
      }),
    })

    // The office to ask about the place. Null when the bot chose the point, or when the bundle
    // has none in reach: `.noPointNearby` is gone, and a card with no point names no office
    // (spec §7, revision 10). The place's own zone and county still carry the alerts.
    switch (snapshot.forecast.kind) {
      case 'forecast':
        context.placeOffice = snapshot.forecast.value.point?.office ?? null
        break
      case 'missing':
        context.placeOffice = snapshot.forecast.point?.office ?? null
        break
      default:
        context.placeOffice = null
        break
    }

    if (place != null) {
      if (!facts.hasStateCode) {
        facts.stateCode = WeatherScreenBuilder.stateCode({ place, readings: snapshot.readings, tables })
        facts.hasStateCode = true
      }
      if (!facts.hasCounty && isGeometryLoaded) {
        const codes = geometry.areaCodes({ containing: place.coordinate })
        const county = codes.find((code) => code.length === 6 && code[2] === 'C') ?? null
        const name = county == null ? null : tables.county(county)?.name
        if (county != null && name != null) {
          facts.county = WeatherAreaName.make({ ugc: county, name: t('weather.area.county', name) })
        }
        facts.zoneUGC = WeatherScreenBuilder.placeZone({ from: codes, stateCode: facts.stateCode, county })
        facts.hasCounty = true
      }
      context.placeStateCode = facts.stateCode
      context.placeCounty = facts.county
      context.placeZoneUGC = facts.zoneUGC
    }

    context.sourceState = snapshot.source == null ? null : states[String(snapshot.source.botID)] ?? null

    if (!isGeometryLoaded) {
      context.needsGeometry = WeatherScreenBuilder.needsGeometry({ hasPlace: place != null, states })
    }
    const stationTowns = { ...request.stationTowns }
    if (snapshot.primaryStation.kind === 'noneNearby') {
      const nearest = snapshot.primaryStation.nearest
      const cached = stationTowns[String(nearest.index)]
      if (cached != null) {
        context.nearestStationTown = cached
      } else {
        const town = WeatherScreenBuilder.town({ for: nearest.station, tables })
        stationTowns[String(nearest.index)] = town
        context.nearestStationTown = town
      }
    }

    // The nearest bundled station is wanted for every place now, not only for one with nothing in
    // reach: a reading from further than `goodReadingKilometres` is not the weather here either,
    // and the page names the station the next packet would go to. Looked up once per place.
    if (place != null) {
      if (!facts.hasNearbyStation) {
        const station = tables.nearestStation({
          toLat: place.coordinate.latitude,
          lon: place.coordinate.longitude,
          within: WeatherPrimaryStation.maxDistanceKilometres,
        })
        facts.nearbyStation =
          station == null
            ? null
            : WeatherNearbyStation.make({
                icao: station.icao,
                name: WeatherNames.stationName(station.name),
                kilometres: MeshWXGeo.distanceKilometres({
                  fromLat: place.coordinate.latitude,
                  fromLon: place.coordinate.longitude,
                  toLat: station.lat,
                  toLon: station.lon,
                }),
              })
        facts.hasNearbyStation = true
      }
      context.nearbyStation = facts.nearbyStation
    }

    // The issue time of every warning any bot holds, newest copy wins: an alert's detail says
    // "issued 1:29 PM" rather than when this phone happened to hear it (spec §3, revision 5).
    for (const state of Object.values(states)) {
      for (const stored of Object.values(state.warnings ?? {})) {
        const key = MeshWXWarningIdentity.key(WeatherStoredWarning.identity(stored))
        // Whichever bot stated a source wins; one that stated none never clears it (spec §2.2,
        // revision 7), the same way an issue time is only ever filled in, never erased.
        if (stored.source !== 0) context.warningSource[key] = stored.source
        if (stored.issuedAt == null) continue
        context.warningIssuedAt[key] = stored.issuedAt
      }
      for (const pending of Object.values(state.pendingUpgrades ?? {})) {
        if (pending.warning.issued_min == null) continue
        context.warningIssuedAt[MeshWXWarningIdentity.key(MeshWXWarningIdentity.of(pending.warning))] =
          pending.warning.issued_min * 60_000
      }
    }

    context.conditions = WeatherConditions.make({
      primary: snapshot.primaryStation,
      nearbyStation: context.nearbyStation,
    })
    context.banner = WeatherWarningBanner.make(snapshot.alerts)
    context.radioRow = WeatherRadioRow.make({ source: snapshot.source, state: context.sourceState, now })

    return WeatherBuildResult.make({
      page: WeatherPageBuild.make({ snapshot, context }),
      placeFacts: facts,
      stationTowns,
    })
  },

  // MARK: - Place

  /**
   * A searched place as it is; a phone fix labelled by the nearest town of 1,000 people within
   * 25 km, else the town of the nearest station within 25 km, else "this location". The label is
   * found once per fix; the kind and radius follow the clock on every build.
   *
   * Swift takes `facts` `inout`; here it is mutated in place, which is the same thing for an
   * object the caller owns.
   */
  resolvePlace(input, { facts, states, tables, now }) {
    switch (input.kind) {
      case 'searched':
        return input.value
      case 'location': {
        const sample = input.value
        const coordinate = WeatherLocationSample.coordinate(sample)
        const label =
          facts.label ??
          WeatherNames.placeLabel({ near: coordinate, tables }) ??
          WeatherScreenBuilder.nearestStationLabel({ to: coordinate, states, tables }) ??
          t('weather.place.thisLocation')
        facts.label = label
        return WeatherPlace.location(sample, { label, now })
      }
      default:
        return null
    }
  },

  nearestStationLabel({ to: coordinate, states, tables }) {
    const indexes = new Set()
    for (const state of Object.values(states)) {
      for (const key of Object.keys(state.observations ?? {})) indexes.add(Number(key))
    }
    let nearest = null
    for (const index of [...indexes].sort((lhs, rhs) => lhs - rhs)) {
      const station = tables.station({ at: index })
      if (station == null) continue
      const kilometres = MeshWXGeo.distanceKilometres({
        fromLat: coordinate.latitude,
        fromLon: coordinate.longitude,
        toLat: station.lat,
        toLon: station.lon,
      })
      if (kilometres > WeatherScreenBuilder.labelReachKilometres) continue
      if (nearest == null || kilometres < nearest.kilometres) nearest = { station, kilometres }
    }
    if (nearest == null) return null
    const town = WeatherScreenBuilder.stationTown(nearest.station, { tables })
    if (town != null) return WeatherNames.placeLabel({ name: town.name, state: town.state })
    return `${WeatherNames.stationName(nearest.station.name)}, ${nearest.station.state}`
  },

  /** "Temple" for KTPL: the town an airport is known by, or the station's own name. */
  town({ for: station, tables }) {
    const town = WeatherScreenBuilder.stationTown(station, { tables })
    return town == null ? WeatherNames.stationName(station.name) : WeatherNames.placeName(town.name)
  },

  /**
   * A regional airport sits outside the town it serves, often closer to a village's census
   * centre: KTPL is nearer Morgans Point Resort than Temple. A sizeable town within 15 km wins.
   */
  stationTown(station, { tables }) {
    return (
      tables.nearestPlace({ toLat: station.lat, lon: station.lon, within: 15, minimumPopulation: 20_000 }) ??
      tables.nearestPlace({ toLat: station.lat, lon: station.lon, within: 15 })
    )
  },

  stateCode({ place, readings, tables }) {
    const coordinate = place.coordinate
    const town = tables.nearestPlace({ toLat: coordinate.latitude, lon: coordinate.longitude, within: 40 })
    if (town != null) return town.state
    const point = tables.nearestPoint({ toLat: coordinate.latitude, lon: coordinate.longitude })
    if (point != null) {
      const state = WeatherNames.pointState(point.name)
      if (state != null) return state
    }
    return readings[0]?.station.state ?? null
  },

  /**
   * The place's own forecast zone: the land zone of its own state, not the water beside it.
   *
   * A coastal place lies in a marine zone as well as its land one — San Juan is in PRZ001 and
   * PRZ016 and in Atlantic zone AMZ712 — and the outlines answer in no particular order, so
   * taking the first zone found could ask a radio for warnings on the sea while the heat advisory
   * sat on the land zone. The place's state decides; its county names that state when nothing
   * else does (docs/MESHWX_UI.md §3.1 U-27).
   */
  placeZone({ from: codes, stateCode = null, county = null }) {
    const zones = codes.filter((code) => code.length === 6 && code[2] === 'Z').sort()
    const state = stateCode ?? (county == null ? null : county.slice(0, 2))
    if (state == null) return zones[0] ?? null
    return zones.find((zone) => zone.startsWith(state.toUpperCase())) ?? zones[0] ?? null
  },

  /**
   * Whether the outlines are worth loading for this page.
   *
   * **A place needs them as much as a warning does.** They used to load only for a held warning
   * with no polygon of its own, which left a place outside the radio's area unable to ask for its
   * alerts at all: the page learns it is outside, and learns the zone and county to ask by, from
   * the outlines — so with none loaded it never asked, and never received the warning that would
   * have loaded them. San Juan sat under "no alerts" while the same radio answered `warn pr` with
   * a heat advisory (docs/MESHWX_UI.md §3.1 U-27).
   */
  needsGeometry({ hasPlace, states }) {
    if (hasPlace) return true
    return Object.values(states).some(
      (state) =>
        Object.values(state.warnings ?? {}).some((stored) =>
          WeatherScreenBuilder.needsOutline(stored.warning),
        ) ||
        Object.values(state.pendingUpgrades ?? {}).some((pending) =>
          WeatherScreenBuilder.needsOutline(pending.warning),
        ),
    )
  },

  needsOutline(warning) {
    return (warning.polygon?.length ?? 0) < 3 && (warning.areas ?? []).length > 0
  },
})
